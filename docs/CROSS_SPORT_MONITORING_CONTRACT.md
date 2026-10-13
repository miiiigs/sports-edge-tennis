# Cross-sport monitoring contract (Tennis-owned) — `sports_edge.monitoring_summary.v1`

Tennis owns the shared performance-monitoring UI and the future cross-sport
reporting interface. This document defines the envelope each sport exposes.
Tennis implements it at `GET /api/dashboard/monitoring-summary` (production
Basic auth, read-only). No Football metrics are defined here. Football fills in
its own `football` block in its own repository, and the shared view reads only
the envelope.

## Envelope (all sports)

| Field | Type | Meaning |
|---|---|---|
| `contract` | string | Always `sports_edge.monitoring_summary.v1`. |
| `sport` | string | `TENNIS`, `FOOTBALL`, … |
| `generated_at` | ISO-8601 UTC | When the payload was computed. |
| `status` | `GREEN` \| `AMBER` \| `RED` | Worst alert severity; `GREEN` when there are no alerts. |
| `alerts[]` | `{code, severity, detail}` | Stable upper-snake `code`, `severity` ∈ `AMBER`/`RED`, human `detail` (no secrets). |
| `read_only` | bool | Always `true`; monitoring never changes betting/model policy. |
| `<sport>` | object | Sport-specific detail (free-form per sport, versioned with the contract). |
| `detail_endpoints` | map | Optional drill-down routes. |

Consumers must ignore unknown fields and unknown alert codes. Show the code and detail, colored by severity.

## Tennis block (`tennis`)

| Field | Source |
|---|---|
| `latest_prediction_cycle_at`, `prediction_cycles_24h` | Finalized production `prediction_runs` (`PRODUCTION_RUN_PREDICATE`). |
| `latest_result_refresh_at` | Latest COMPLETED `ingest_runs` with `api_tennis_completed_result_refresh`. |
| `provider_feeds[]` | Per `(source_system, source_dataset)`: latest completed/failed run, runs, failed and partial in 24h. |
| `odds[]`, `latest_sharp_observed_at`, `latest_offered_observed_at` | `odds_snapshots` freshness by class (SHARP/OFFERED) and bookmaker. |
| `oddspapi_usage` | Month-to-date `oddspapi_request_ledger` attempts by status. |
| `pending` | `pending_result_diagnostics`: totals by reason, age bucket and flag. |
| `calibration_flags` | Existing `calibration_monitor` flags (advisory). |

## Tennis alert codes

| Code | Severity | Rule |
|---|---|---|
| `PREDICTION_CYCLE_STALE` | RED | No finalized production run within 90 min. |
| `RESULT_REFRESH_STALE` | RED | No completed API-Tennis result refresh within 180 min. |
| `SHARP_REFERENCE_STALE` | RED | No SHARP/PINNACLE snapshot within 6 h; Type A bets cannot qualify. |
| `OFFERED_ODDS_STALE` | AMBER | No offered snapshot within 6 h. |
| `PROVIDER_FEED_FAILURES` | AMBER | Any FAILED ingest run in 24 h (one alert per feed). |
| `PENDING_RESULTS_STRANDED` | AMBER | Pending picks older than the 3-UTC-date recovery window. |
| `PENDING_RESULTS_DELAYED` | AMBER | Started ≥6 h ago, still re-observed, still no terminal result. |
| `CALIBRATION_<FLAG>` | AMBER | Each calibration monitor flag. |

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
