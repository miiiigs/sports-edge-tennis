# API-Tennis + OddsPapi provider pairing — readiness, cutover and rollback

Selected production pairing: **API-Tennis** (fixtures, results, player/tournament ids,
surfaces, history, match stats) + **OddsPapi** (match-winner market `121`, Pinnacle
`pinnacle+30` sharp reference, offered books `1xbet`/`unibet`/`bet365`, snapshots, CLV).
Sportradar and GoalServe are not intended production providers.

**Status (2026-10-13): implemented and tested, not enabled.** Production still runs
`API_TENNIS + LEGACY_SPLIT`, and Pinnapi has returned `HTTP_403` on every hourly run.
The last Pinnacle sharp snapshot was taken at 2026-10-01 19:05Z, so no Type A bet has
been able to qualify since then.

## What the pairing does

| Concern | Behavior |
|---|---|
| Activation | `TENNIS_ODDS_PROVIDER_MODE=ODDSPAPI_TENNIS` (alias `ODDSPAPI`) is accepted with API-Tennis data **only** when `TENNIS_ODDS_ONLY_AUTHORIZATION=API_TENNIS_ODDSPAPI_V1` is set. Without it, `load_provider_authority` raises. |
| Readiness (`evaluate_cutover_readiness`) | Checks `API_TENNIS_API_KEY`, the core schema, `TENNIS_ODDS_FAILOVER_AUTHORIZATION=ODDSPAPI_ODDS_V1`, `ODDSPAPI_API_KEY`, the migration 0015 ledger and identity-cache tables, and verified ODDSPAPI SHARP/PINNACLE + OFFERED mappings. It never requires Sportradar configuration. |
| Circuit / failover | None. Decision `circuit=DISABLED reason=ODDS_ONLY_NO_FAILOVER`. An OddsPapi failure never falls back to Pinnapi. |
| Pinnapi | Never called: `pinnapi_key` is `None` in this odds mode. API-Tennis offered odds are not fetched either. |
| Spend gate | Zero Manager consensus → zero OddsPapi requests (Rule A). |
| Identity | Discovery runs for cold consensus matches (it was previously Betradar-gated, so it never ran under API-Tennis). The shared reconciler is used read-only. Matching uses deterministic participant names in exactly one orientation plus start time ≤60 min. A match claimed by two fixtures, or any ambiguous match, stays uncached. The cache stores the reconciler's DIRECT/REVERSED orientation; it used to be hard-coded to DIRECT. |
| Durable links | Written only at ingestion, inside one transaction per event. A second OddsPapi fixture for an already-linked match is a `CONFLICT`. A participant-bridge conflict rolls the event back and is counted, and the cycle continues. |
| Prices | Market 121 only. A quote needs `active` and a price above 1. Quotes at or after match start are rejected. Freshness is enforced by the evaluator's `odds_max_staleness_minutes`. Raw rows and snapshots are idempotent through `ON CONFLICT`. Odds never reach agents; they are consumed only by Market, Veto, Type B, CLV and shadow. |
| Budget | Per cycle ≤5 requests (default), and the discovery call now counts against the cap. Monthly ledger: 5000 with a 500 reserve. A planned overrun or ledger exhaustion → `ODDSPAPI_BUDGET_BLOCKED`, no quotes, zero bets. |
| HTTP errors | 429/5xx: bounded retry (each attempt is ledgered). 401/403/other 4xx: no retry. Batch log: `OFFERED_ODDS_UNAVAILABLE provider=ODDSPAPI_TENNIS kind=HTTP_403 class=FORBIDDEN`. No quotes, so every decision is no-bet. Keys never appear in logs. |

## Evidence collected without paid requests (2026-10-13)

- Production DB (read-only): ODDSPAPI mappings exist: `pinnacle+30`→SHARP/PINNACLE, `1xbet`/`unibet`/`bet365`→OFFERED. API-Tennis `PNCL` is OFFERED only, never sharp. Ledger and identity-cache tables exist. Ledger: 117 requests, 2026-09-24..26 (shadow); none in October.
- Shadow replay of all 18 stored OddsPapi events through the name/time path:
  - 18/18 participant names match in the correct orientation ("Surname, Given" vs "G. Surname").
  - Point-in-time start delta is 0 min for all 18.
  - 0 ambiguous, 0 disagreeing with recorded links.
  - The 3 events whose matches carry an API-Tennis link resolved to exactly the recorded match. The other 15 were out of replay scope (Sportradar-era matches without an API-Tennis link).
- Tests: `tests/test_api_tennis_oddspapi_pairing.py` (PostgreSQL identity integration, discovery unit, batch wiring, 403/429 handling) plus the updated authority, circuit, reconciliation and warm-cache suites.

## Remaining gates before enabling (need approval)

1. **Live readiness probe.** It costs OddsPapi quota, so it needs an approved budget. Run one bounded discovery call (1 request) and confirm:
   - the account is active (not 401/403);
   - the `/fixtures` shape;
   - coverage of today's API-Tennis consensus matches;
   - name/time links and orientation for those matches.
   Then run one qualification call (4 requests) and confirm Pinnacle +30 prices on market 121.
2. Review and merge the draft PR.
3. Owner approval for the provider variable change.

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
