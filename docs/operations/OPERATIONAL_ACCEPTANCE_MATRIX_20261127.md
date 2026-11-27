# Operational acceptance matrix — 27 November 2026

**Scope:** independent monitoring, work-product receipts, settlement scheduling,
external-service readiness and the January rehearsal.
**Decision:** **conditional, not activated.** Implementation and isolated tests
pass; every external dependency remains owner-gated.
**Cross-PR:** PR #389 (`8c17249`), PR #391 (`0777fbd`), PR #394 (`9343a75`).

## 1. Delivery matrix

| Area | Implementation | Evidence | Status |
|---|---|---|---|
| Durable work-product receipt | `ops/work_product_receipts.py` | 11 unit + 3 PostgreSQL tests | PASS (isolated) |
| Database row verification per stage | SQL against real tables | PostgreSQL integration | PASS (CI) |
| Partial / missing / unverified zero | fail-closed statuses | receipt unit tests | PASS |
| Seven-stage coverage | discovery, forecast, bookmaker, archive, official, bookmaker settlement, candidate | stage matrix | PASS (with 394 tables/manifests) |
| Settlement scheduling | `ops/settlement_schedule.py` | 14 tests | PASS |
| Sporting / bookmaker separation | separate planners, approved rules only | tests | PASS |
| Source authorization / orientation / revisions / reschedule / stale | explicit guards | tests | PASS |
| External service shape | deployment manifest + acceptance doc | this PR | PREPARED |
| Verified clocks | `ops/clock_health.py` | 5 tests | PASS |
| Durable storage / backup / restore | `ops/durable_store.py` | 4 tests | PASS |
| Credential rotation | documented procedure | acceptance doc | PREPARED |
| Incident / recovery | monitoring adapter | 14-scenario matrix | PASS (isolated) |
| January rehearsal | `validation/january_rehearsal.py` | 5 tests, 13 scenarios | PASS / 1 BLOCKED |
| January scope | `specs/january_2027_scope_20261127.json` | 10 tests | PASS |
| BO5 chronology | `validation/grand_slam_bo5_chronology.py` | 17 tests | SYNTHETIC ONLY |
| Activity metadata | `validation/activity_context.py` | 12 tests | PASS |

## 2. Actual test results

| Suite | Local result |
|---|---|
| `test_work_product_receipts.py` | 11 passed |
| `test_work_product_receipts_postgres.py` | 3 skipped locally (no DSN); run in PostgreSQL Foundation CI |
| `test_settlement_schedule.py` | 14 passed |
| `test_external_service_primitives.py` | 9 passed |
| `test_january_rehearsal.py` | 5 passed |
| Existing PR #391 suite | full suite green locally and in CI |
| Existing PR #389 suite | full suite green locally and in CI |

## 3. Independent service provisioning requirements

| Requirement | Estimate / decision |
|---|---|
| Scheduler separate from `tennis-live-runner` | required |
| Runtime | 15-minute cron, < 5 minutes per cycle |
| Credentials | server-side secret store; 90-day rotation |
| Clock | independent HTTP `Date`, max 60 s skew, every cycle |
| Storage | persistent `/data`, >= 1 GB, ~20 MB/month |
| Backup | daily, separate destination, monthly restore drill |
| Notification | at least two channels; SMS/phone optional |
| Healthchecks.io | Hobbyist $0/month (20 checks); Business $20/month if SMS/phone |
| Railway monitor | $0–5/month incremental (verify) |
| Volume + backup | $0–2/month |
| **Total estimate** | **$0–27/month**, not authorised |

No account, plan, heartbeat, notification or external backup was created.

## 4. January release blockers

1. AO 2027 edition-specific rulebook not located; carry-forward needs owner
   confirmation.
2. 2027 entry lists and draws not published.
3. AO qualifying exact first match not published.
4. `get_draw` + `include_qualification` entitlement unverified; no provider call.
5. Lucky-loser / replacement draw diff not implemented.
6. Official result source and canonical participant-order contract not approved.
7. Bookmaker grading rules not approved.
8. Owner approval, activation and cost authorisation not requested.

## 5. Phase 1 acceptance

| Requirement | Result |
|---|---|
| Real receipt publisher for rehearsals | implemented |
| Verify persisted rows before success | implemented for every supported stage |
| Discovery | yes |
| Forecast execution | three-agent, on-time, completed run |
| Bookmaker acquisition | BET decision + price + snapshot |
| Archive acknowledgement | 394 ledger or verified manifest |
| Official settlement | visible result + non-pending settlement |
| Bookmaker settlement | only approved rules; otherwise acknowledged empty |
| Prospective candidate execution | expectations matched to originals |
| Missing / partial / zero | never success |

## 6. Phase 2 acceptance

| Requirement | Result |
|---|---|
| Separately testable settlement schedule | yes, pure planner + dry-run CLI |
| Source authorization | blocked without approval/reference |
| Canonical participant ordering | blocked on mismatch |
| Revisions | same revision deduped; newer revision corrects append-only |
| Rescheduled fixtures | result before revised start blocked |
| Stale outcomes | blocked beyond approved window |
| Sporting vs bookmaker | separate pathways, no shared grade |
| No inferred bookmaker result | explicit grade + approved rule required |

## 7. Phase 3 acceptance

| Requirement | Result |
|---|---|
| Separation | manifest and runbook |
| Secure authentication | server-side secret names, TLS, no inbound listener |
| Verified clocks | code + tests |
| Durable storage | append-only store + tests |
| Backup / restoration | online backup, manifest, restore drill + tests |
| Credential rotation | documented procedure |
| Independent health checks | 7-stream adapter |
| Incident notification | webhook + failure/duplicate/recovery tests |
| Recovery procedures | runbook + tests |
| Costs | estimated, not purchased |

## 8. Phase 4 acceptance

| January scenario | Result |
|---|---|
| First ATP fixture | PASS |
| First Challenger fixture | PASS |
| AO BO5 main draw | PASS |
| Qualifying BO3 classification | PASS |
| Withdrawal / replacement | PASS |
| Lucky loser | BLOCKED (draw diff) |
| Rescheduled starts | PASS |
| Provider failure | PASS (not an empty slate) |
| Missing fixture | PASS (denominator retained) |
| Archive unavailable | PASS (receipt fails) |
| Settlement correction | PASS (append-only) |
| Scheduler non-execution | PASS |
| No universal January penalty | PASS |

## 9. Decision

**Operational connections are implemented and testable; activation is not
approved.** The next action is owner review of this matrix, the deployment
manifest and the January blockers. No production activation, invented
monitoring event, unauthorised provider request or expenditure occurred.
