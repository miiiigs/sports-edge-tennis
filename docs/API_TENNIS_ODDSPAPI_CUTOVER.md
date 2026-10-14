# API-Tennis + OddsPapi provider pairing — readiness, cutover and rollback

Selected production pairing: **API-Tennis** (fixtures, results, player/tournament ids,
surfaces, history, match stats) + **OddsPapi** (match-winner market `121`, Pinnacle
`pinnacle+30` sharp reference, offered books `1xbet`/`unibet`/`bet365`, snapshots, CLV).
Sportradar and GoalServe are not intended production providers.

**Status (2026-10-14): implemented, tested and live-probed (10/10 acceptance PASS), not
enabled.** Production still runs `API_TENNIS + LEGACY_SPLIT`, and Pinnapi has returned
`HTTP_403` on every hourly run. The last Pinnacle sharp snapshot was taken at
2026-10-01 19:05Z and the last API-Tennis offered snapshot at 2026-10-10 21:00Z (see
"Why API-Tennis offered odds stopped"), so no Type A bet has been able to qualify.

## What the pairing does

| Concern | Behavior |
|---|---|
| Activation | `TENNIS_ODDS_PROVIDER_MODE=ODDSPAPI_TENNIS` (alias `ODDSPAPI`) is accepted with API-Tennis data **only** when `TENNIS_ODDS_ONLY_AUTHORIZATION=API_TENNIS_ODDSPAPI_V1` is set. Without it, `load_provider_authority` raises. |
| Readiness (`evaluate_cutover_readiness`) | Checks `API_TENNIS_API_KEY`, the core schema, `TENNIS_ODDS_FAILOVER_AUTHORIZATION=ODDSPAPI_ODDS_V1`, `ODDSPAPI_API_KEY`, the migration 0015 ledger and identity-cache tables, and verified ODDSPAPI SHARP/PINNACLE + OFFERED mappings. It never requires Sportradar configuration. |
| Circuit / failover | None. Decision `circuit=DISABLED reason=ODDS_ONLY_NO_FAILOVER`. An OddsPapi failure never falls back to Pinnapi. |
| Pinnapi | Never called: `pinnapi_key` is `None` in this odds mode. API-Tennis offered odds are not fetched either. |
| Spend gate | Zero Manager consensus → zero OddsPapi requests (Rule A). |
| Identity | Discovery runs for cold consensus matches (it was previously Betradar-gated, so it never ran under API-Tennis). The shared reconciler is used read-only. Matching uses deterministic participant names in exactly one orientation plus start time ≤60 min. A match claimed by two fixtures, or any ambiguous match, stays uncached. The cache stores the reconciler's DIRECT/REVERSED orientation; it used to be hard-coded to DIRECT. |
| Durable links | Written only at ingestion, inside one transaction per event, with the same ≤60 min window as discovery. A read-only preview pass first drops any canonical match claimed by two fixtures in the same batch, independent of payload order. A second OddsPapi fixture for an already-linked match is a `CONFLICT`. A participant-bridge conflict rolls the event back and is counted, and the cycle continues. |
| Prices | Market 121 only. A quote needs `active` and a price above 1. Quotes at or after match start are rejected. Freshness is enforced by the evaluator's `odds_max_staleness_minutes`. Raw rows and snapshots are idempotent through `ON CONFLICT`. Odds never reach agents; they are consumed only by Market, Veto, Type B, CLV and shadow. |
| Budget | Per cycle ≤5 **billable attempts**, retries included. One `OddspapiAttemptCap` is shared by the discovery and qualification ledger bindings and is checked before the ledger row is reserved and before any socket opens. Discovery counts. The plan is also checked up front. Monthly ledger: 5000 with a 500 reserve. A planned overrun, an exhausted cap or ledger exhaustion → `ODDSPAPI_BUDGET_BLOCKED`, no quotes, zero bets. |
| HTTP errors | 429/5xx: bounded retry (each attempt is ledgered and draws from the cap). 401/403/other 4xx: no retry. Batch log: `OFFERED_ODDS_UNAVAILABLE provider=ODDSPAPI_TENNIS kind=HTTP_403 class=FORBIDDEN`. No quotes, so every decision is no-bet. Keys never appear in logs. |
| Other failures | Any other exception in the OddsPapi block (DB integrity, ledger write, parsing) logs `OFFERED_ODDS_UNAVAILABLE provider=ODDSPAPI_TENNIS kind=IMPLEMENTATION class=<type>`. Zero bets, but Market, Type B and CLV close evidence still run for the cycle. |
| Readiness keys | The exact key the batch requests, `pinnacle+30`, must be SHARP/PINNACLE. None of `1xbet`/`unibet`/`bet365` may be SHARP, and at least one must be OFFERED. |

## Evidence collected without paid requests (2026-10-13)

- Production DB (read-only): ODDSPAPI mappings exist: `pinnacle+30`→SHARP/PINNACLE, `1xbet`/`unibet`/`bet365`→OFFERED. API-Tennis `PNCL` is OFFERED only, never sharp. Ledger and identity-cache tables exist. Ledger: 117 requests, 2026-09-24..26 (shadow); none in October.
- Shadow replay of all 18 stored OddsPapi events through the name/time path:
  - 18/18 participant names match in the correct orientation ("Surname, Given" vs "G. Surname").
  - Point-in-time start delta is 0 min for all 18.
  - 0 ambiguous, 0 disagreeing with recorded links.
  - The 3 events whose matches carry an API-Tennis link resolved to exactly the recorded match. The other 15 were out of replay scope (Sportradar-era matches without an API-Tennis link).
- Tests: `tests/test_api_tennis_oddspapi_pairing.py` (PostgreSQL identity integration, discovery unit, batch wiring, 403/429 handling) plus the updated authority, circuit, reconciliation and warm-cache suites.

## Dry-run evidence (2026-10-14, read-only, 0 requests)

- Rebased onto `main` 757c43e (#227 + #229). `tennis-live-runner` and `tennis-dashboard` run 757c43e with `TENNIS_DATA_PROVIDER_MODE=API_TENNIS`, `TENNIS_ODDS_PROVIDER_MODE=LEGACY_SPLIT`, `TENNIS_ODDS_FAILOVER_AUTHORIZATION=ODDSPAPI_ODDS_V1`; `TENNIS_ODDS_ONLY_AUTHORIZATION` is unset. Merging this PR therefore changes nothing until the variables change.
- `evaluate_cutover_readiness` against production with the proposed cutover variables: `ready=True`, no blockers (ledger and identity-cache schema present, `pinnacle+30` SHARP/PINNACLE, three OFFERED keys).
- Probe dry run: mappings 4/4 PASS, 11 upcoming consensus matches (all Challenger, 2026-10-14 03:00–22:00 UTC), stop reason `DRY_RUN_NO_REQUESTS`, `attempts_used=0`.
- Ledger: 117 rows, all 2026-09 (109 SUCCESS, 8 FAILURE, PRODUCTION shadow); 0 in 2026-10. Identity cache: 6 rows.
- Live odds currently in production: newest SHARP snapshot is Pinnapi PINNACLE at 2026-10-01 19:05 UTC; newest API-Tennis OFFERED snapshots are 2026-10-10 21:00 UTC; OddsPapi OFFERED last 2026-09-26. No live odds in the last 24 h, so no Type A bet can currently qualify.
- Coverage risk: the scope is Challenger-only. The 2026-09 shadow linked 3 of 18 events, so criterion 10 may come back FAIL on coverage alone; that would be a real finding, not a probe defect.

## Live probe result (2026-10-14, 5 billable requests, owner-approved)

Run from `5d0fc01` with the workstation wrapper. Correlation id
`probe:20261014T0606Z:4853029d`. Ledger rows 126–130: 1 `fixtures` DISCOVERY + 4
`tournament odds` QUALIFICATION (`pinnacle+30`, `1xbet`, `unibet`, `bet365`, chunk 3),
all `DIAGNOSTIC`, HTTP 200, `retry_sequence=0`. October total: 5. No raw observations,
snapshots, identity-cache rows or source ids were written.

- Acceptance: **10/10 PASS**.
- Scope: 11 upcoming consensus matches (run 1164, all Challenger).
- Discovery: 43 tennis fixtures (06:35–17:30Z), 5 `EXACT`, 38 `UNMATCHED` (other events,
  doubles, WTA), 0 contested, 0 reconciliation errors.
- Matched 5/11, all DIRECT, tournaments 30869, 40709, 52078. Start deltas are 0 min, except
  132137 at 20 min.

| match | players (OddsPapi P1 / P2) | pinnacle+30 | 1xbet | unibet | bet365 |
|---|---|---|---|---|---|
| 132126 | Martinez, Pedro / Dodig, Matej | 2.46 / 1.595 | 2.39 / 1.51 | 2.38 / 1.55 | 2.50 / 1.47 |
| 132127 | Moller, Elmer / Neumayer, Lukas | 2.06 / 1.826 | 2.02 / 1.69 | 2.07 / 1.72 | 2.20 / 1.58 |
| 132128 | Passaro, Francesco / Torres, Tiago | 1.289 / 3.80 | 1.26 / 3.485 | 1.26 / 3.65 | 1.27 / 3.40 |
| 132137 | Guerrieri, Andrea / Kasnikowski, Maks | 1.598 / 2.42 | 1.60 / 2.196 | 1.62 / 2.23 | 1.58 / 2.20 |
| 132148 | Jianu, Filip Cristian / Kolar, Zdenek | 2.10 / 1.769 | 2.079 / 1.67 | 2.12 / 1.68 | 2.20 / 1.58 |

All 20 markets were OPEN and pre-match, received 2026-10-14 06:06:56Z. OddsPapi `changedAt`
ranged from 2026-10-11 21:57Z (unibet) to 2026-10-14 05:34Z.

**Coverage gap: 6 of 11 consensus matches were absent from discovery.** They were 132164,
132165 and 132166 at 14:00Z, 132160 at 18:00Z, 132140 at 20:30Z and 132163 at 22:00Z
(South-American Challengers). The request window reached 2026-10-15 00:00Z, but no
`pinnacle+30` fixture after 17:30Z came back. The most likely reason is that Pinnacle had
not listed them yet at 06:06Z. Proving it would take another request. Hourly cycles retry
cold matches, so coverage should rise closer to start. Watch `cold_rejected` after cutover.

## Why API-Tennis offered odds stopped (2026-10-10 21:00Z)

Root cause: a control-flow coupling, not an API-Tennis outage.

- `run_live_mvp` calls Pinnapi only when the slate has Manager consensus. Pinnapi returns
  `HTTP_403` (logged as `non-200 response` before #218), and `LivePilotPinnapiFailed` is
  raised after predictions are written.
- `batch.py` catches only the API-Tennis and Sportradar failures per slate. The Pinnapi
  failure escapes to the outer `except LiveMvpError` and returns exit 2
  (`LIVE_OPERATIONAL_HEALTH status=ERROR live_batch_status=2`, Railway `CRASHED`). The
  API-Tennis offered leg is never reached.
- DB evidence: from 2026-10-09 18:00Z, API-Tennis `LIVE_ODDS` ingests occur **only** in
  cycles with zero consensus. Those were runs 1090 and 1101–1109, plus the
  no-prediction-run cycles at 20:03Z and 21:00Z on 2026-10-10. Every cycle with consensus
  ≥1 has none. Since 2026-10-10 21:00Z every cycle has had consensus (11 per run today).
- Not the cause: API-Tennis fixtures, results and stats ingests still complete hourly (last
  2026-10-14 05:04Z), so the key, quota and auth are fine. Polling cadence and freshness
  filters play no part, because the fetch never runs.
- Side observation: each successful offered cycle also logged ~10–20 `FAILED` per-event
  `ingest_runs` beside the `COMPLETED` ones. This is unrelated to the stoppage and needs a
  separate look.
- Under `ODDSPAPI_TENNIS` the runner passes `pinnapi_key=None`, so this abort path disappears
  with the cutover. Fixing it under `LEGACY_SPLIT` (treat a Pinnapi failure as "no sharp"
  and continue) is a separate change.

## Remaining gates before enabling (need approval)

1. ~~Live readiness probe~~ done 2026-10-14, 10/10 PASS (above).
2. Review and merge the draft PR.
3. Owner approval for the provider variable change.

## Live readiness probe (procedure)

CLI: `python -m sports_edge_tennis.live.oddspapi_probe` (`live/oddspapi_probe.py`).

| Step | Endpoint | Planned requests |
|---|---|---|
| 0 | none: reads the latest finalized production run's upcoming Manager-consensus matches and the ODDSPAPI mappings | 0 |
| 1 | `GET /v4/fixtures` (window around those starts, `bookmakers=pinnacle+30`) | 1 |
| 2 | read-only reconcile (`persist=False`, ≤60 min, exactly one orientation; contested matches dropped) | 0 |
| 3 | `GET /v4/odds-by-tournaments`, one chunk of ≤5 accepted tournaments × `pinnacle+30`, `1xbet`, `unibet`, `bet365` | 4 |
| | **Total (hard cap)** | **≤5** |

- The cap is enforced in code. `--max-requests` above 5 is refused. Every attempt, including 429/5xx retries, draws from one `OddspapiAttemptCap` before a socket opens, and multi-id 404 fallbacks are disabled. A retry therefore lowers the number of quotes collected, never raises spend.
- With zero upcoming consensus matches it stops before step 1 and reports zero requests. It also stops before step 3 if no fixture is accepted.
- It never writes mappings, identity-cache rows, raw observations or snapshots. The only writes are 1–5 `oddspapi_request_ledger` rows (classification `DIAGNOSTIC`) under one `probe:` correlation id.
- Without `--execute` it makes no request; use that as a free dry run first.

Commands (on approval only):

Run from a checkout of this branch (the deployed runner image does not contain the probe until merge):

```
# free: shows consensus scope and mapping state, makes no OddsPapi request
railway run --service tennis-live-runner -- python ops/validation/oddspapi_readiness_probe_local.py
# billable, ≤5 requests
railway run --service tennis-live-runner -- python ops/validation/oddspapi_readiness_probe_local.py --execute
```

`railway run` supplies `ODDSPAPI_API_KEY`. The runner's `TENNIS_DATABASE_URL` is a private `*.railway.internal` host, so the wrapper builds the Postgres TCP-proxy DSN in memory from `railway variables --service Postgres-Sdqz` and refuses `--dsn`. Nothing secret is printed; the start line shows only `api_key_present=True|False`. `--max-requests N` (N ≤ 5) lowers the cap further.

Verify spend afterwards (read-only):

```sql
SELECT request_purpose, attempt_status, http_status, retry_sequence, count(*)
FROM oddspapi_request_ledger WHERE correlation_id = '<probe correlation_id>'
GROUP BY 1, 2, 3, 4;
```

It prints one `ODDSPAPI_PROBE {json}` line, including `attempts_used` and verdicts for the ten acceptance criteria:
1. authentication;
2. valid tennis discovery;
3. canonical match;
4. orientation;
5. no ambiguous or duplicate link accepted;
6. Pinnacle sharp mapping;
7. offered mappings;
8. quotes open and pre-match;
9. within the request cap;
10. a match with both a sharp and an offered open quote, without Pinnapi.

Criteria that could not be reached are `NOT_EVALUATED`, never `PASS`. Freshness is judged by receive time by design (see "Prices"). The probe therefore checks that markets are OPEN and pre-match, not OddsPapi's `changedAt`.

Run it while the hourly slate has upcoming consensus matches (check the dry run first). Otherwise it truthfully reports zero qualified requests.

## Cutover (only after approval)

Set on `tennis-live-runner` only (`tennis-live-failover-once` is currently an idle `print` command and needs no change):

```
TENNIS_ODDS_PROVIDER_MODE=ODDSPAPI_TENNIS
TENNIS_ODDS_ONLY_AUTHORIZATION=API_TENNIS_ODDSPAPI_V1
TENNIS_ODDS_FAILOVER_AUTHORIZATION=ODDSPAPI_ODDS_V1   # already set
ODDSPAPI_API_KEY=<already set>
```

Keep `TENNIS_DATA_PROVIDER_MODE=API_TENNIS` and `TENNIS_MANAGER_POLICY_VERSION=v1`.

Expected first-run log lines:
- `LIVE_PROVIDER_AUTHORITY data_provider=API_TENNIS odds_provider=ODDSPAPI_TENNIS circuit=DISABLED reason=ODDS_ONLY_NO_FAILOVER`
- `SCHEMA_ODDSPAPI_LEDGER status=…` (not `SKIPPED_LEGACY_ODDS`)
- `ODDSPAPI_IDENTITY_CACHE warm=… cold=… cold_discovered=… cold_rejected=…`
- `OFFERED_ODDS provider=ODDSPAPI_TENNIS requests=≤5 …`

There should be no `LIVE_PILOT_PINNAPI_FAILED`.

Watch for the first 24h:
- ledger requests per hour stay ≤5;
- `cold_rejected` and `conflict` counts;
- the latest SHARP/PINNACLE `odds_snapshots.source_observed_at` advances;
- Type A decisions carry `decision_odds_snapshots` and CLV evidence.

## Rollback

1. Remove `TENNIS_ODDS_ONLY_AUTHORIZATION` and set `TENNIS_ODDS_PROVIDER_MODE=LEGACY_SPLIT` (the previous state; Pinnapi is still 403, so this restores "no sharp reference", not betting).
2. No data rollback is needed. OddsPapi links, cache rows, ledger rows and snapshots are append-only provenance. Decisions and settlements are never rewritten.
3. To quarantine a bad link, delete the specific `oddspapi_event_identity_cache` row (identity only, never prices). Durable `match_source_ids` corrections go through a reviewed identity-correction change, never by hand in production.
