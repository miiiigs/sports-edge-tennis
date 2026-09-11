# Sports Edge Tennis Production Handoff

Status: final stabilization candidate. This document contains no credentials.

## Canonical source target

Repository:

```text
miiiigs/sports-edge-tennis
```

Canonical target branch after the final reconciliation gate:

```text
main
```

Final stabilization candidate before merge:

```text
integration/final-stabilization-livebase-2026-09
PR #58
```

Do not maintain separate product behavior on the old dashboard/live feature branches after `main` is canonical.

## Railway project

Project:

```text
sports-edge-tennis
```

Production application services:

| Service | Current Git source before reconciliation | Runtime purpose |
|---|---|---|
| `tennis-dashboard` | `feat/tennis-client-dashboard` | FastAPI client dashboard |
| `tennis-live-runner` | `feat/tennis-live-scheduled-runner` | hourly bounded live prediction/market runner |
| `tennis-validation-runner` | `feat/tennis-live-scheduled-runner` | bounded history/result refresh utility |

### Dashboard

Current health check:

```text
/healthz
```

Current unauthenticated start command before owner-auth enablement:

```text
python -m pip install . && python -m uvicorn sports_edge_tennis.dashboard.app:app --host 0.0.0.0 --port $PORT
```

Owner-auth start command, only after authorized owner bootstrap configuration is ready:

```text
python -m pip install . && python -m uvicorn sports_edge_tennis.dashboard.owner_access:app --host 0.0.0.0 --port $PORT
```

### Live runner

Current start command:

```text
python -m pip install . && python -m sports_edge_tennis.live.batch --days 3
```

Current schedule:

```text
0 * * * *
```

That is one bounded run per hour. Do not add polling/retry loops merely to increase scan frequency.

### Validation/history runner

Current start command:

```text
python -m pip install . && python -m sports_edge_tennis.live.history_refresh --start-date 2026-01-01 --end-date 2026-09-09 --chunk-days 31
```

This is a bounded utility, not a second live scheduler.

## Provider dependencies

Required live-runner secret variable names:

- `API_TENNIS_API_KEY`
- `PINNAPI_KEY`
- `TENNIS_DATABASE_URL`

Dashboard database variable:

- `TENNIS_DATABASE_URL`

Do not commit provider credentials or database DSNs to Git.

## Authentication state

Owner-only dashboard authentication exists in code but is disabled by default.

Auth variables:

- `TENNIS_DASHBOARD_AUTH_ENABLED`
- `TENNIS_DASHBOARD_AUTH_ALLOW_SIGNUP`
- `TENNIS_DASHBOARD_AUTH_ALLOWED_EMAILS`
- `TENNIS_DASHBOARD_AUTH_COOKIE_SECURE`

Follow `docs/TENNIS_DASHBOARD_AUTH.md` for bootstrap and rollback.

Do not enable owner auth until the authorized owner email is confirmed. The owner chooses their own password; no password belongs in Railway variables or this repository.

## Database authority gate

Two PostgreSQL services are currently visible in the Railway project:

- `Postgres`
- `Postgres-Sdqz`

`Postgres-Sdqz` exposes a 5 GB persistent volume at `/var/lib/postgresql/data` through the current connector. `Postgres` does not expose a mounted volume through that same connector view.

This is useful topology evidence but is **not sufficient to delete either database**.

All three application services expose a `TENNIS_DATABASE_URL` variable name, but the connected Railway OAuth surface redacts its rendered value/reference. Before retiring any database, confirm in Railway that the dashboard, live runner, and validation runner reference the same intended PostgreSQL service.

Required proof before DB retirement:

1. Inspect each application's `TENNIS_DATABASE_URL` variable reference in Railway.
2. Confirm all three point to the intended same service.
3. Confirm that database contains the historical corpus and current live rows.
4. Take/verify a recoverable backup or Railway snapshot where supported.
5. Only then mark the other service unused.
6. Database deletion must be a separate deliberate operation, never bundled with source reconciliation.

## Verified pre-reconciliation rollback points

Dashboard known-good production commit:

```text
c8a6cbb17a8055e9891e4567a088212aaaf2069d
```

Live runner / validation known-good production lineage after durable Type B:

```text
09f058f0512ecd95f8011a49fbb2b21af84022ea
```

Keep these values during source reconciliation. If a service regression occurs, restore its previous Git source/commit and previous start command before attempting a new fix.

## Verified live behavior on the Type B production baseline

A successful production batch after the Type B deployment recorded:

```text
slates=2
failed_slates=1
fixtures=276
targets=8
consensus=1
price_candidates=16
pinnapi_requests=1
offered_requests=1
offered_quotes=62
bets=1
type_b_value_signals=0
```

The same run persisted:

```text
TYPE_B_LEDGER scan_run_id=1 inserted=0 persisted=0
```

The third slate was unavailable because the provider response did not contain the expected result list; the batch surfaced that explicitly rather than fabricating data.

## Source reconciliation order

After PR #58 is green and merged to `main`:

1. Record the merge SHA as the new canonical candidate and retain the rollback SHAs above.
2. Change only `tennis-dashboard` Git source branch to `main`.
3. Preserve its existing unauthenticated start command until owner access is ready.
4. Verify deployment success and `/healthz`.
5. Verify `/today`, `/history`, and `/performance` read persisted data without provider calls.
6. Change only `tennis-live-runner` Git source branch to `main`.
7. Preserve the hourly cron and existing command.
8. Verify the next bounded scheduled execution completes and persists expected diagnostics.
9. Change only `tennis-validation-runner` Git source branch to `main`.
10. Preserve its bounded history-refresh command unless a new refresh window is explicitly required.
11. Re-check all three services against the same authoritative `TENNIS_DATABASE_URL` reference.
12. Only after those checks should obsolete feature branches be treated as non-authoritative.

The currently connected Railway tool can inspect service sources and update runtime configuration, but does not expose an action for changing an existing service's GitHub source branch. If that capability remains unavailable, perform only the branch-selector step manually in Railway UI. Do not simulate it by force-moving old feature branch refs.

## Model and decision invariants

The reconciliation is operational only. It must not change:

- Agent 1/2/3 model logic merely to increase outputs
- Manager C1-C4 thresholds
- Type A value thresholds
- Type B price-dispersion thresholds
- Veto trigger thresholds
- Pinnacle sharp-reference policy

A low-volume or zero-signal scan is valid evidence and must be persisted as such.

## Known evidence gaps after stabilization

The system does not currently have trustworthy canonical evidence for every external Veto condition in the original build specification. In particular, do not fabricate:

- recent injury reports
- medical timeouts
- exact previous-match end time
- exact >3h15 previous-match duration

Veto V1 records unsupported checks as unavailable.

The next substantive evidence workstream after source stabilization is prospective closing-price capture and CLV lifecycle completion. Historical model accuracy/ROI-style metrics must not be presented as proof of live profitability.
