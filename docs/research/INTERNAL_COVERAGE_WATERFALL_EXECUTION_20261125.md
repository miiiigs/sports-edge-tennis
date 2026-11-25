# Internal fixture coverage waterfall and 57-target reconciliation — 25 November 2026

**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Verified starting head:** `d72c569c64d22a1172c69cf530140886fc33e008`
**Execution mode:** authorised read-only PostgreSQL (`default_transaction_read_only=on`), no provider calls, no scraping, no writes, no migrations, no deployment.
**Evidence JSON:** `docs/research/evidence/internal_coverage_waterfall_20261125.json`
**Execution script:** `ops/validation/internal_fixture_coverage_waterfall_20261125.py`

## Exact execution

The DSN was resolved in memory from the linked Railway Postgres service and supplied through `TENNIS_DATABASE_URL`; no credential is printed or committed. The script sets a read-only transaction.

```powershell
$env:TENNIS_DATABASE_URL = <authorised read-only DSN resolved in memory>
python ops/validation/internal_fixture_coverage_waterfall_20261125.py \
  --archive-json railway_forward_20261124_full_day.json \
  --archive-json railway_forward_20261125_0000_0400Z.json \
  --out-json docs/research/evidence/internal_coverage_waterfall_20261125.json
```

The archived log exports were read from PR #390 commit `f75e13919d28bad01c86b4a6788ff096b7749b5a` without modifying that branch.

Validation executed:

```text
python -m pytest tests/test_licensed_fixture_census.py -q
12 passed, 1 skipped in 2.96s
python -m py_compile ops/validation/internal_fixture_coverage_waterfall_20261125.py
COMPILE_OK
```

## Scope warning

**These are internal stored-provider records, not an independent official denominator.** The census counts below describe what the internal database saw and did. They do not measure how many official ATP/Challenger fixtures existed. No licensed complete fixture export or permitted independent official feed was available, so official totals and true discovery recall remain `UNKNOWN`, never zero.

## Daily internal coverage waterfall, 14–25 November 2026

Scheduled start UTC day, latest match version per canonical match, main draw only. `PIT-visible` means a version with `as_of`, `source_observed_at` and `ingested_at` all at or before start minus 30 minutes.

| UTC day | Tour | Stored | PIT-visible | Targeted | A1 predicted | A2 predicted | A3 predicted | Manager consensus | Settled | Cancelled | No result |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 11-14 | ATP | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| 11-14 | CH | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 13 | 0 | 1 |
| 11-15 | ATP | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| 11-15 | CH | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 |
| 11-16 | ATP | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| 11-16 | CH | 26 | 0 | 0 | 0 | 0 | 0 | 0 | 25 | 1 | 0 |
| 11-17 | ATP | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| 11-17 | CH | 75 | 73 | 69 | 69 | 50 | 0 | 13 | 68 | 6 | 0 |
| 11-18 | ATP | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| 11-18 | CH | 43 | 42 | 42 | 42 | 33 | 0 | 12 | 38 | 2 | 1 |
| 11-19 | ATP | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| 11-19 | CH | 32 | 32 | 32 | 32 | 25 | 0 | 5 | 32 | 0 | 0 |
| 11-20 | ATP | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| 11-20 | CH | 28 | 28 | 28 | 28 | 23 | 0 | 7 | 28 | 0 | 0 |
| 11-21 | ATP | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| 11-21 | CH | 15 | 15 | 15 | 15 | 12 | 0 | 3 | 14 | 0 | 0 |
| 11-22 | ATP | 1 | 1 | 1 | 1 | 0 | 0 | 0 | 1 | 0 | 0 |
| 11-22 | CH | 7 | 7 | 7 | 7 | 6 | 0 | 0 | 7 | 0 | 0 |
| 11-23 | CH | 29 | 29 | 29 | 29 | 17 | 0 | 6 | 25 | 4 | 0 |
| 11-24 | ATP | 2 | 1 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| 11-24 | CH | 78 | 77 | 77 | 77 | 47 | 0 | 11 | 20 | 4 | 55 |
| 11-25 | CH | 51 | 51 | 51 | 51 | 35 | 0 | 10 | 6 | 0 | 45 |

### Aggregate main draw versus qualifying

| Stage | Main draw | Qualifying | Total |
|---|---:|---:|---:|
| Stored fixtures | 424 | 250 | 674 |
| PIT-visible by start-30m | 366 | 123 | 489 |
| Targeted by original first run | 363 | 123 | 486 |
| Targeted on time | 362 | 123 | 485 |
| A1 predicted | 363 | 123 | 486 |
| A2 predicted | 248 | 38 | 286 |
| A3 predicted | 0 | 0 | **0** |
| Manager consensus | 67 | 13 | 80 |
| Settled completed | 298 | 229 | 527 |
| Cancelled | 17 | 7 | 24 |
| No result | 104 | 4 | 108 |

### Agent and Manager availability

For the 486 original first-run targets in the window:

| Object | Status | Count |
|---|---|---:|
| Agent 1 | PREDICTED | 486 |
| Agent 2 | PREDICTED / ABSTAINED | 286 / 200 |
| Agent 3 | ABSTAINED | 486 |
| Manager | CONSENSUS | 80 |
| Manager | NO_BET — `TWO_OR_MORE_ABSTENTIONS` | 200 |
| Manager | NO_BET — `AGENT3_ABSTENTION_SPREAD_EXCEEDED` | 124 |
| Manager | NO_BET — `DIRECTIONAL_DISAGREEMENT` | 82 |

A3's 486 abstentions split as: 406 `INSUFFICIENT_MATCHUP_SUPPORT`, 78 `UNKNOWN_SURFACE`, 2 `WEAK_SIGNAL`.

### Publication and settlement

- Aligned Model Picks in the window: **161 distinct matches**, all on time (picked before start minus 30 minutes), **0 late**. All 161 are Challenger; there are no ATP main-draw aligned picks in this window.
- Result status for the 674 internal fixtures: **527 COMPLETED**, **15 RETIRED**, **1 WALKOVER**, **23 CANCELLED**, **108 NO_RESULT**.
- The large 24–25 November `NO_RESULT` counts are expected: those fixtures were still pending at report time, not failed settlements.

### The real coverage gap

The waterfall isolates a sharp discovery/ingestion gap:

- 14–16 November: 53 stored fixtures, **0 PIT-visible**, **0 targeted**. The fixtures exist in the database, but no version was visible before the 30-minute deadline, so no original run could target them.
- 17 November onward: 362/424 main-draw fixtures PIT-visible and 362 targeted on time. Coverage is near-complete once the feed is populated.
- Across the whole window, 3 PIT-visible main-draw fixtures were not targeted, and 1 targeted fixture was not on time.

This is an internal fixture-discovery/deadline gap, not an official-fixture shortage. The official denominator is unknown.

The specific cause is now established in
[`FIXTURE_DISCOVERY_DEADLINE_ROOT_CAUSE_20261125.md`](FIXTURE_DISCOVERY_DEADLINE_ROOT_CAUSE_20261125.md):
the live batch scheduler wrote no heartbeats or scheduled-fixture runs from
`2026-11-12T23:01:16Z` to `2026-11-16T23:05:34Z`, missing 95 expected hourly
executions (13 Nov 00:00Z – 16 Nov 22:00Z). A manual redeploy at
`2026-11-16T23:03:11Z` restored the schedule; the first post-gap fetch was a
retrospective catch-up that persisted already-completed events with a fetch-time
observation clock. It was not a provider request failure, a late refresh, or a
deployment/cron-config change.

## Reconciliation of the 57 archived forward targets

The two PR #390 log exports contain **57 unique archived forward targets** (51 from 24 November full day plus 6 from 25 November 00:00–04:00Z).

| Reconciliation result | Count |
|---|---:|
| Matched archive to database | 21 |
| Rescheduled since archive | 36 |
| Disappeared / no match version | **0** |
| Agent status mismatch | **0** |
| Manager status mismatch | **0** |
| First-run ID changed | **0** |
| Tour mismatch | **0** |

All 57 canonical IDs still exist. All archived original agent statuses and Manager statuses still match the original database rows for the archived run. The only material change is schedule: 36 of 57 moved after the archive snapshot.

### Archived Manager reasons and publication

| Archived Manager reason | Targets | Aligned pick | No aligned pick |
|---|---:|---:|---:|
| `TWO_OR_MORE_ABSTENTIONS` | 21 | 0 | 21 |
| `AGENT3_ABSTENTION_SPREAD_EXCEEDED` | 17 | 16 | 1 |
| `DIRECTIONAL_DISAGREEMENT` | 11 | 0 | 11 |
| `NONE` (consensus) | 8 | 7 | 1 |
| **Total** | **57** | **23** | **34** |

The 23 aligned picks are 16 tracking-only `A1_A2_WIDE_SPREAD` and 7 `MANAGER_CONSENSUS`. The 34 missing picks are explained by recorded agent abstention/directional-disagreement reasons, not by missing fixtures or missing Manager rows.

### Archived outcomes at report time

- 2 targets had a COMPLETED result.
- 55 had no result yet at the 25 November report time.
- 0 archived targets were cancelled or had disappeared.

The archive is a forward snapshot, not a settled performance cohort. No accuracy or ROI claim is made from 2 settled matches.

### Rescheduling detail

- 456 of 674 internal fixtures (67.7%) have more than one distinct scheduled start; 648 have more than one version.
- Distinct-start distribution: 218 matches with 1 start, 150 with 2, 122 with 3, 83 with 4, 41 with 5, 31 with 6, 12 with 7, 4 with 8, 5 with 9, 3 with 10, 4 with 11, 1 with 12.
- Examples from the 57 archive: `134321` 16:00 → 16:10; `134324` 16:00 → 22:30 (6 starts); `134343` 24 Nov 18:00 → 25 Nov 16:00; `134354` 25 Nov 08:00 → 26 Nov 08:00.
- A changed start is not a cancellation. No absent fixture was inferred as void.

## Missing player mappings

- 0 of the 674 internal fixtures lack a provider source ID.
- 0 players referenced by those fixtures are absent from `players`.
- 0 players lack a non-`ODDSPAPI` historical source identity.
- However, 15 A3 diagnostic evaluations in the window are marked `LIVE_UNRESOLVED` (7,134 are `RESOLVED`). This is a model-identity bridge gap, not a missing provider fixture ID.
- The model live-to-historical identity bridge contains only 1 `BRIDGE` event; cross-provider resolution is primarily alias-based. A complete cross-provider player-mapping certificate is still unavailable.
- A3's support gap in this window is graph support/surface availability, not a missing canonical player ID: 406 insufficient matchup support and 78 unknown surface.

## Existing census adapter executed against stored records

The existing `licensed_fixture_census` adapter was executed in an explicit internal mode:

- source system `API_TENNIS`, 674 source rows scheduled 14–25 November 2026;
- `provider_declares_complete_window = false`;
- `official_denominator_available = false`;
- `external_publication_verified = false`;
- every official total in the adapter output is `null`.

Adapter classification for the 674 source rows:

| Classification | Rows |
|---|---:|
| `OUT_OF_SCOPE` (qualifying) | 250 |
| `EXACT_ID_BUT_SCHEDULE_MISMATCH` | 304 |
| `LATE_SOURCE_PUBLICATION` | 58 |
| `NO_PICK_WITH_AGENT_ABSTENTION` | 31 |
| `ALIGNED_PICK` | 24 |
| `NO_ON_TIME_TARGET` | 4 |
| `CONSENSUS_NO_ALIGNED_PICK` | 3 |

The 304 `EXACT_ID_BUT_SCHEDULE_MISMATCH` rows are not missing fixtures. The adapter compares the PIT version at the 30-minute deadline with the latest stored schedule; 304 of 424 main-draw fixtures were rescheduled between those two observations. That is consistent with the 67.7% reschedule rate above. It is a schedule-reconciliation result, not an identity failure.

The adapter confirms the same internal coverage picture and, correctly, refuses to emit an official denominator. It does not convert missing completeness into zero coverage.

## Independent official denominator

**Blocked.** No authorised independent official fixture source is available.

- No licensed complete ATP/Challenger men's main-draw export was supplied.
- No permitted official feed was called.
- No restricted official website was scraped.
- The existing census adapter was executed only in an explicit internal mode: it reuses the existing `daily_waterfall` and `source_matching` logic against stored API-Tennis records, with `provider_declares_complete_window = False`, so `official_main_draw_total` and `official_knowable_total` remain `null`.

Therefore:

- Internal stored coverage: measured above.
- Independent official coverage/discovery recall: **unknown, not zero**.
- The 57 archived targets are internally reconcilable but cannot be declared a complete official slate.

## Remaining blockers

1. No authorised independent official fixture denominator or licensed complete export.
2. No durable contemporaneous forward receipt: the PR #390 log export was retrieved after observation and is not an append-only collector.
3. Schedule rescheduling is extensive (67.7% of internal fixtures), so scheduled-day cohorts and issue-day cohorts diverge.
4. The 24–25 November cohort is not settled yet; no performance claim is possible.
5. Cross-provider player mapping has only one explicit bridge event; alias resolution is not a complete mapping certificate.

## Verdict

The internal coverage waterfall is now executed with real stored records. It shows a clear 14–16 November PIT/deadline gap, near-complete targeting from 17 November, zero A3 predictions across the window, material A2 abstention (200/486), 80 Manager consensus decisions, 161 on-time aligned picks, and 527 settled results. The 57 archived targets reconcile exactly by identity and agent/Manager status; 36 are rescheduled and none disappeared. This is **internal fixture coverage**, not an independent official census. The official denominator remains a documented blocker.

## Evidence hashes

```text
docs/research/evidence/internal_coverage_waterfall_20261125.json
SHA256 37C9F49C2C35FE8AAF09FEDBC75185C450A4157EA68C9ADEB8B68E88DCFC8EF3
bytes  598357
```
