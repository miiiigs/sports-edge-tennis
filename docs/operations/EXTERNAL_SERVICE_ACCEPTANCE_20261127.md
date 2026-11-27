# External monitoring service — deployment requirements and acceptance

**Date:** 27 November 2026
**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Status:** prepared, **NOT ACTIVATED**. No account, paid plan, heartbeat,
notification, external backup or credential was created.
**Manifest:** [`ops/monitoring/deployment_manifest_20261127.json`](../../ops/monitoring/deployment_manifest_20261127.json)
**Runbook:** [`INDEPENDENT_MONITORING_ADAPTER_20261127.md`](INDEPENDENT_MONITORING_ADAPTER_20261127.md)
**Code:** `src/sports_edge_tennis/ops/clock_health.py`,
`src/sports_edge_tennis/ops/durable_store.py`,
`ops/validation/publish_work_product_receipt.py`

## 1. Deployment shape

```text
Railway cron (or equivalent), separate service
  schedule  */15 * * * *
  runtime   < 5 minutes
  inbound   none
  outbound  heartbeat service + approved notifier
  volume    /data (state, receipts, backups)
```

The service must **not** run inside `tennis-live-runner`. A monitor hosted by
the runner is silent when the runner is silent.

The pipeline publishes a durable receipt only after SQL verification. The
monitor reads the receipt and sends a success heartbeat. A missing receipt is a
failure (`RECEIPT_MISSING`), never a success.

## 2. Authentication and secrets

- Credentials live only in the hosting provider's secret store.
- Required names are listed in `ops/monitoring/healthchecks.env.example`.
- The ping key, check UUID, notifier URL and database DSN must never appear in
  Git, logs, screenshots, the state file or a receipt.
- TLS is required for heartbeat and notification endpoints.
- There is no inbound listener, so no inbound authentication is exposed.

**Rotation:** create a second check/key, deploy it to the monitor only, confirm
two successful cycles, keep a 24-hour overlap, then disable the old check.
Record the date and operator, never the secret. Default interval: 90 days.

## 3. Verified clocks

The monitor compares its UTC clock with an independent HTTP `Date` response
every cycle (`clock_health.py`):

- threshold: 60 seconds;
- `CLOCK_OK` only when every independent source is within threshold;
- `CLOCK_SKEW_EXCEEDED` or `CLOCK_UNVERIFIED` exits 2 and sends no success;
- a wrong clock cannot make a late receipt timely or an early alert normal.

Tests cover an in-threshold reading, a five-minute skew, a missing source, a
missing `Date` header and multiple sources using the worst skew.

## 4. Durable storage, backup and restore

Receipts and state live on `--state-path` and `/data/receipts` on a persistent
volume. The append-only SQLite store uses WAL and `synchronous=FULL`.

`durable_store.py` provides:

- append-only triggers (update and delete abort);
- online backup with `sqlite3.backup` (never a filesystem copy of a live WAL);
- an integrity check and inventory SHA-256 manifest;
- restore into a **new** path with a manifest comparison;
- failure removes only the newly created destination.

Operational requirements:

| Item | Requirement |
|---|---|
| Volume | >= 1 GB, persistent across deploys |
| Growth | ~20 MB/month estimated |
| Retention | 400 days |
| Backup | daily to a separately approved destination |
| Restore drill | monthly, with the preserved manifest |
| Partial backup | deleted, never treated as valid |
| Live WAL copy | forbidden |

Tests cover append-only enforcement, backup/restore inventory equality,
non-overwrite of an existing destination and manifest-mismatch failure.

## 5. Health checks and incidents

One check per stream (seven), plus the live runner check. Recommended:

- cron schedule `0 * * * *`, UTC, grace 45 minutes;
- repeat reminder every 60 minutes while down;
- minimum two independent notification channels;
- SMS/phone escalation only if the owner approves the paid plan;
- one recovery notification per incident; duplicates suppressed.

The existing acceptance matrix proves duplicate suppression, recovery, notifier
failure (exit 2, state not advanced), monitor-unavailable (exit 2) and the
95-slot independent non-execution simulation.

## 6. Estimated operating requirements and costs

Public list prices checked 27 November 2026; **verify before purchase**.

| Item | Estimate |
|---|---|
| Healthchecks.io Hobbyist | $0/month, 20 checks (8 needed) |
| Healthchecks.io Business | $20/month, 100 checks + SMS/phone credits; $192/year |
| Railway monitor runtime | $0–5/month incremental on the existing plan (verify usage pricing) |
| Durable volume (1 GB) | $0–1/month |
| Backup storage (1 GB) | $0–1/month |
| **Recommended range** | **$0–27/month** |

The Hobbyist plan is sufficient for email/chat heartbeats. The Business plan is
only justified if the owner wants SMS/phone escalation. No purchase is made or
authorised by this document.

## 7. Recovery procedure

1. Confirm the monitor itself is alive with an independent ping.
2. Check the forecasting scheduler deployment and last receipt.
3. Check read-only database reachability.
4. Recover the pipeline; wait for the next successful receipt.
5. Confirm one recovery notification and no duplicate pages.
6. Never backdate a missed forecast, insert a target retrospectively or rewrite
   a settlement.

## 8. Acceptance matrix

| Requirement | Evidence | Status |
|---|---|---|
| Separate from forecasting scheduler | manifest `must_not_run_inside`; deployment example | PASS (design) |
| No inbound listener | manifest; cron-only command | PASS (design) |
| Server-side credentials | env example; redaction tests | PASS |
| Credential rotation procedure | manifest + this document | PASS (procedure) |
| Verified clock | `clock_health.py` + 5 tests | PASS |
| Durable append-only storage | `durable_store.py` + trigger test | PASS |
| Backup and restore | `durable_store.py` + 3 tests | PASS |
| Independent health checks | 7-stream monitoring adapter | PASS (isolated) |
| Incident notification | webhook path + notifier-failure test | PASS (isolated) |
| Recovery | recovery/dedupe/restart tests | PASS (isolated) |
| Account / paid plan | none created | **NOT ACTIVATED** |
| External backup destination | none approved | **BLOCKED** |
| Owner approval | not requested | **BLOCKED** |

## 9. Activation gates

All false in the manifest:

```text
owner_approval                  false
provider_account                false
paid_plan                       false
notifier_destination            false
durable_volume                  false
backup_destination              false
clock_verification              false
receipt_publisher_wired         false
test_alert_rehearsed            false
restore_drill_completed         false
credential_rotation_recorded    false
```

## 10. Explicit non-claims

- No account was created, no plan purchased and no heartbeat sent.
- No external backup was written.
- Costs are estimates, not quotes.
- The service is not yet a verified independent clock authority.
