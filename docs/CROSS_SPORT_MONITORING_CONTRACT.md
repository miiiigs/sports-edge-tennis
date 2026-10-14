# Cross-sport monitoring contract (Tennis-owned) — `sports_edge.monitoring_summary.v1`

Tennis owns the shared performance-monitoring UI and the future cross-sport
reporting interface. This document defines the envelope each sport exposes.
Tennis implements it at `GET /api/dashboard/monitoring-summary` (production
Basic auth, read-only). No Football metrics are defined here. Football fills in
its own document in its own repository; Tennis translates it server-side into
this envelope (see "Football adapter" below), and the shared view reads only the
envelope.

## Envelope (all sports)

| Field | Type | Meaning |
|---|---|---|
| `contract` | string | Always `sports_edge.monitoring_summary.v1`. |
| `sport` | string | `TENNIS`, `FOOTBALL`, … |
| `generated_at` | ISO-8601 UTC | When the payload was computed. |
| `status` | `GREEN` \| `AMBER` \| `RED` \| `UNKNOWN` | Worst alert severity; `GREEN` when there are no alerts. `UNKNOWN` only when the sport's monitoring could not be read (adapter states); never shown as healthy. |
| `alerts[]` | `{code, severity, detail}` | Stable upper-snake `code`, `severity` ∈ `AMBER`/`RED`, human `detail` (no secrets). |
| `read_only` | bool | Always `true`; monitoring never changes betting/model policy. |
| `<sport>` | object | Sport-specific detail (free-form per sport, versioned with the contract). |
| `detail_endpoints` | map | Optional drill-down routes. |

Consumers must ignore unknown fields and unknown alert codes. Show the code and detail, colored by severity.

## Tennis block (`tennis`)

| Field | Source |
|---|---|
| `latest_prediction_cycle_at`, `prediction_cycles_24h`, `prediction_cycles_with_predictions_24h` | Finalized production `prediction_runs` (`PRODUCTION_RUN_PREDICATE`); the last counts runs that wrote at least one prediction. |
| `latest_result_refresh_at` | Latest COMPLETED `ingest_runs` with `api_tennis_completed_result_refresh`. |
| `provider_feeds[]` | Per `(source_system, source_dataset)`: latest completed/failed run, runs, failed and partial in 24h. |
| `odds[]`, `latest_sharp_observed_at`, `latest_offered_observed_at` | `odds_snapshots` with `odds_origin='LIVE_PROVIDER'` only, by class (SHARP/OFFERED), provider and bookmaker. Historical/synthetic backfills never refresh it. |
| `oddspapi_usage` | Month-to-date `oddspapi_request_ledger` attempts by status. |
| `pending` | `pending_result_diagnostics`: totals by reason, age bucket and flag. |
| `calibration_flags` | Existing `calibration_monitor` flags (advisory). |

## Tennis alert codes

| Code | Severity | Rule |
|---|---|---|
| `PREDICTION_CYCLE_STALE` | RED | No finalized production run within 90 min. |
| `RESULT_REFRESH_STALE` | RED | No completed API-Tennis result refresh within 180 min. |
| `SHARP_REFERENCE_STALE` | RED | No live SHARP snapshot (any provider) within 6 h; Type A bets cannot qualify. Independent of result freshness. |
| `OFFERED_ODDS_STALE` | AMBER | No offered snapshot within 6 h. |
| `PROVIDER_FEED_FAILURES` | AMBER | Any FAILED ingest run in 24 h (one alert per feed). |
| `PENDING_RESULTS_STRANDED` | AMBER | Pending picks older than the 3-UTC-date recovery window. |
| `PENDING_RESULTS_DELAYED` | AMBER | Started ≥6 h ago, still re-observed, still no terminal result. |
| `PENDING_RESULTS_UNMATCHED` | AMBER | Pending picks whose result cannot be matched: `NO_API_TENNIS_LINK` or `TERMINAL_FIXTURE_WITHOUT_RESULT`. |
| `PREDICTION_CYCLES_EMPTY` | AMBER | Finalized production runs in 24 h, none wrote a prediction. |

`FIXTURE_POSTPONED`, `AWAITING_RESULT`, `NOT_STARTED` and the `RESCHEDULED` flag
never alert: they are expected states, not ingestion failures.

The endpoint opens its session with `default_transaction_read_only=on` and returns
503 with no error detail when the database fails.
| `CALIBRATION_<FLAG>` | AMBER | Each calibration monitor flag. |

## Football adapter (`football_monitoring_v1` → this envelope)

Football (`sports-edge-ai` PR #122) serves `GET /api/monitoring/football` with its
own schema `football_monitoring_v1`. Tennis does not change it.
`performance/football_monitoring_adapter.py` (`adapt_football_monitoring`)
translates it server-side. It takes the result of the unified dashboard's
read-only Football client (Tennis PR #208, `football_client.request_get`), so it
does no transport of its own.

| Football | Envelope |
|---|---|
| `schema_version == "football_monitoring_v1"` | Required, otherwise `UNKNOWN` + `FOOTBALL_CONTRACT_MISMATCH`. |
| `generated_at` (aware ISO-8601) | `source_generated_at`. Missing or unparseable: `UNKNOWN` + `FOOTBALL_CONTRACT_MISMATCH`. Older than 15 min: `FOOTBALL_MONITORING_STALE` (AMBER). More than 5 min in the future: `FOOTBALL_MONITORING_CLOCK_SKEW` (AMBER). |
| `operational.alerts[] {code, …}` | `FOOTBALL_<code>` with a fixed severity (table below); context keys become `detail`. Unknown codes: `FOOTBALL_UNMAPPED_<code>`, AMBER, kept. |
| `operational` / `operational.alerts` missing, `read_only` not true | `FOOTBALL_FIELD_MISSING` (AMBER). |
| Selected `operational` fields, `sources`, `deployed_commit` | Passed through untouched under `football`. `model_performance` and `qualified_betting` stay in Football. |

| Football alert | Severity |
|---|---|
| `SCHEDULER_STALE`, `NO_READY_PREDICTIONS_FOR_UPCOMING`, `ALL_ODDS_STALE`, `FIXTURE_PROVIDER_NOT_CONNECTED` | RED |
| `SCHEDULER_LAST_CYCLE_ERROR`, `READY_FIXTURES_WITHOUT_PICK`, `SETTLEMENT_LAG`, `PROVIDER_QUOTA_NOT_HEALTHY` | AMBER |

| Client state (PR #208) | Envelope |
|---|---|
| `NOT_CONFIGURED` | `UNKNOWN` + `FOOTBALL_NOT_CONFIGURED` |
| `AUTH_REQUIRED` (401/403) | `UNKNOWN` + `FOOTBALL_MONITORING_AUTH_FAILED` |
| `TIMEOUT`, `UNAVAILABLE`, `HTTP_ERROR`, other | `UNKNOWN` + `FOOTBALL_MONITORING_UNAVAILABLE` |
| `MALFORMED` | `UNKNOWN` + `FOOTBALL_CONTRACT_MISMATCH` |
| `NOT_ALLOWED` | `UNKNOWN` + `FOOTBALL_MONITORING_NOT_ALLOWLISTED` |

Security: the token (`SPORTS_EDGE_CLIENT_API_TOKEN`, sent by the PR #208 client as a
Bearer header) and the Football base URL stay server-side. The adapter output never
contains either, and the browser never calls Football.

Wiring, when PR #208 lands (not done here):
1. Add `/api/monitoring/football` to `football_client.ALLOWED_GET_PATHS`. Football
   PR #122 already allowlists it for the read-only client token.
2. Serve `adapt_football_monitoring(request_get("/api/monitoring/football"))` from an
   owner-authenticated Tennis route, for example `/api/dashboard/monitoring-summary/football`.

Contract regression tests: `tests/test_football_monitoring_adapter.py`. Its fixture
mirrors Football's `build_football_monitoring_payload` keys at PR #122 head.

## Separation rules

- Forward-issued production evidence only: experiments, backtests and unfinalized runs are excluded by `PRODUCTION_RUN_PREDICATE`. Historical backfills never count as forward performance.
- Model Picks and Value Bets stay separate (`/model-pick-results`, `/value-bet-results`). A "Strong Value Bet" tier does not exist in Tennis today and is not invented here.
- Stakes shown anywhere are the persisted quarter-Kelly fraction, capped at 2% (`QUARTER_KELLY_CAP_2_V1`). Monitoring never recomputes or changes them.

## 2026-10-13 production reading

`status=RED`:
- `SHARP_REFERENCE_STALE`: last SHARP/PINNACLE snapshot 2026-10-01 19:05Z, caused by Pinnapi HTTP 403.
- `OFFERED_ODDS_STALE`: last API-Tennis offered odds 2026-10-10 21:00Z.
- `PENDING_RESULTS_STRANDED`: 13 picks.
- Calibration flags.

Prediction cycles (26 in 24 h) and the result refresh were current.
