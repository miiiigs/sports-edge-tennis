# Tennis production audit — 2026-10-15

Read-only audit of Railway project `sports-edge-tennis` (production) and DB
`Postgres-Sdqz` against `main` `a823832`. No production data, variables or
deployments were changed. No paid provider calls were made. DB queries ran with
`default_transaction_read_only=on`. Secret checks printed booleans only.

## 1. Runtime health

| Service | State | Evidence |
|---|---|---|
| `tennis-live-runner` | Healthy | Deployment `f621c023` (2026-10-14 08:45Z). 16/16 hourly runs 09:00Z → 00:01Z exited `LIVE_BATCH_SUCCESS`. One failed slate (09:02Z, 2026-10-16: `api-tennis response missing result list`). No tracebacks, OOMs or restarts. |
| `tennis-validation-runner` | Healthy, schedule drift | Cron `10 8 * * *` (08:10 UTC). Docs said 00:10. Runs 10-09 00:15Z, 10-10 00:14Z, **none 10-11**, then 10-12/13/14 ~08:10Z. Checkpoint `API_TENNIS_RECENT_HISTORY_V1` next_start 2026-10-14, so there is no data hole. |
| `tennis-dashboard` | Healthy | `/` and `/api/*` → 401 without credentials; `/healthz` → 200. |
| `Postgres-Sdqz` | Healthy | 0.9/4.9 GB volume. `Postgres` (pg16) is the unused empty DB. |
| `tennis-sportradar-identity-apply-v2` | CRASHED, **obsolete** | Single deployment 2026-09-23. It ran `ops/validation/sportradar_retirement_detail_probe_20260923.py` (not an identity apply) and died on a Sportradar non-200. Sportradar is not a production provider and the runner has no Sportradar key. Safe to delete on owner approval. |
| `tennis-sportradar-terminal-probe` | "Online", idle | Last log 2026-09-25. |
| `tennis-replay-v2-final` | **Public, unauthenticated** | `python -m http.server` directory listing of replay `model_picks.json`, `value_bets.json` etc. No credentials exposed. Remove the public domain or stop the service. |

Noise: every run logs pip's "running as root" warning at `error` level.

## 2. Intermittent all-slate rejection (`unsupported_event_status`)

The 2026-10-15 slate alternated between `ingested=29` (18:05, 19:03, 22:06,
00:02Z) and `ingested=0` (20:04, 21:05, 23:01Z). In the zero runs, exactly the
29 in-scope fixtures (same event keys, e.g. `12171133`) were rejected as
`unsupported_event_status`. The 2026-10-16 slate shows the same 9 vs 0 flip.
The 23:01Z run had `targets=0`, created no prediction run, and caused the
00:01Z `LIVE_BATCH_GAP_ALERT age_minutes=114`.

Blank statuses are already promoted only for safe future fixtures, and a blank
would be `missing_required_field`. So the provider is sending a non-blank token
outside `_STATUS_MAP`. The raw value was never persisted. Commit `3a5eb82` now
persists `raw_event_status` and logs `FIXTURE_UNSUPPORTED_STATUS`. The status is
still never mapped. Decide the mapping after the first observed values.

## 3. Data integrity

Clean: no duplicate decisions, predictions or Manager rows per run/match; no
duplicate settlements or CLV rows; no `(source_system, source_match_id)`
mapped to two matches; no duplicate odds snapshots.

**Cross-provider duplicate canonical matches.** These are the same player pair
within 36 h existing as two `match_id`s, nearly all one Sportradar-ingested plus
one API-Tennis.

| latest outcome pair | pairs | with BET | both predicted |
|---|---|---|---|
| COMPLETED/COMPLETED | 105 | 8 | 6 |
| -/COMPLETED | 28 | 0 | 17 |
| -/- | 9 | 0 | 9 |
| other (cancelled/retired) | 17 | 0 | 8 |

The range is 2026-02-23 → 2026-09-29, concentrated in the 2026-09-17..28
Sportradar window. `data/agent1_history.py` ranks results per `match_id` only,
so the 105 double-completed matches enter Elo history twice. This is a
model-input change and was **not** fixed here. It needs an approved dedupe
(e.g. honour `cross_provider_match_reconciliations`, 14 rows) plus an Agent
1/3 replay.

**Pending results.** All 10 "pending" rows are one match: **130717**,
K. Smith v E. Winter, San Diego 2 Qualification, 2026-09-21 22:40Z. Its 10 BET
rows are hourly re-issues. The last fixture observation was 2026-09-21 22:05Z
(SCHEDULED). There is no result observation, and no duplicate canonical copy
holds one. It is linked `API_TENNIS:12164591`, `SPORTRADAR:sr:sport_event:74867008`
and `PINNAPI`. It sits outside the pending-refresh recovery window (10-12..10-14).
Draft PR #223 targets this class. No result was invented.

**"459 eligible decisions" are rows, not wagers.** 459 BET rows cover 58
distinct matches (one side each). Settlement totals (213 W / 218 L) count
re-issues. The dashboard dedupes to the first bet per match.

`paper_executions` (9 rows, last 2026-09-13) comes from a superseded pre-main
lineage. Nothing on `main` writes it.

## 4. Prediction and value performance (forward, live runs only)

Type A, first BET per match:

| metric | value |
|---|---|
| settled W/L | 55 (26 W / 29 L), +1 void, +1 cancelled, +1 pending |
| win rate | 47.3% vs mean model p 70.2% vs mean implied 54.2% |
| flat ROI | −16.8% |
| quarter-Kelly ROI | −15.0% (stake-weighted) |
| mean CLV | −6.6% over 48 bets; 2% positive |
| dashboard all-time | 57 bets, 26/28, −8.21 u, ROI −14.7% (extra production/publication filters) |

Mean claimed edge is 30–40%, while CLV is negative on 98% of bets. The
divergence from the market is model error, not edge. Shadow strategy
`A1_A2_AGREE_A3_ABSTAIN_V1`: 97 settled, ROI −21.9%, mean CLV −6.9%.

The dashboard's "aligned live history" (+1.66 u on 44 bets) applies today's
60% floor retroactively to Sep 10+ decisions. It is hindsight-selected and
must not be read as a forward record.

Agents (last pre-start prediction per match, completed results):

| agent | predicted | abstained | accuracy | Brier |
|---|---|---|---|---|
| A1 surface Elo | 1484 | 0 | 64.4% | 0.223 |
| A2 hierarchical Markov | 905 | 579 | 62.9% | 0.229 |
| A3 intransitivity | 7 | 1477 | — | — |

A3 abstention reasons since Oct 1: `INSUFFICIENT_MATCHUP_SUPPORT` 6554, null
944, `WEAK_SIGNAL` 26. A2: `INSUFFICIENT_HISTORY` 805, `UNSUPPORTED_OR_UNKNOWN`
445, null 156.

Manager consensus calibration (first consensus per match): p 0.55 → hit 0.51,
0.64 → 0.68, 0.74 → 0.80, 0.84 → 0.68, 0.94 → 0.77. Calibrated in the middle,
overconfident above 0.8.

**Consensus policy reality.** Production Manager is `v1`
(`allow_agent3_abstention=True`). All 459 BET rows are 2 active + 1 abstaining.
Since 2026-10-01: CONSENSUS 1575 (all 2+1), NO_BET with 3 active 94. A strict
3/3 policy (v2) would currently issue essentially no bets.

## 5. OddsPapi

October ledger: 70 PRODUCTION (10 discovery, 60 qualification, 1×404) plus 5
DIAGNOSTIC (the 10-14 probe), so 75 billable. Per cycle 4–5 requests, 0 on
zero-consensus cycles. Projected full month at the current rate is about
3.3–3.7k, under the 4,500 usable.

Snapshots since cutover: PINNACLE sharp, 1XBET and BET365 current to
00:02Z 10-15; UNIBET to 22:06Z. No duplicate snapshots. Raw observations
301 rows / 221 distinct payloads (unchanged prices re-stored).

Defect fixed in commit `5278458`: `month_to_date_count` excluded all DIAGNOSTIC rows, so
real probe requests did not draw from the allowance. DIAGNOSTIC rows now count
once they record a provider outcome. Unsent synthetic reservations still do not.

Consumption lever: qualification re-prices every consensus tournament hourly
up to 3 days out. Tiering refresh by time-to-start (e.g. every 3 h beyond
24 h) would roughly halve spend without touching close capture.

## 6. Security and CI

- CI `PostgreSQL Foundation`: green on `main` and all recent PRs.
- GitGuardian failures on PRs #219/#220 (`0d0a25f`, `adc890c`) are test-fixture
  literals in `tests/test_dashboard_production_owner_composition.py`. The
  production Basic password is set, at least 15 bytes, and equals none of
  them. No production secret value (API-Tennis, OddsPapi, dashboard, Postgres)
  occurs anywhere in `git log --all -p`. Mark the incidents as test
  credentials in GitGuardian.
- `ODDSPAPI_API_KEY` is also present on `tennis-dashboard` and
  `Postgres-Sdqz`, which do not call OddsPapi. Narrow its scope.
- Code/secret scanning are disabled on the GitHub repo.
- Open PRs: #225 (deferred, do-not-merge), #223 (draft, conflicting; pending
  result recovery), #208 (draft), #197 (ready, mergeable, from 09-27), #183 and
  #170 (conflicting, stale).
