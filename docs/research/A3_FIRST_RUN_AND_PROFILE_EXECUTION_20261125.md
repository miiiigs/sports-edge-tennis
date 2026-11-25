# Agent 3 first-run support diagnostics and historical profile execution — 25 November 2026

**Branch / PR:** `research/a3-first-issue-live-soft-shadow-20261123` (PR #385)
**Verified starting head:** `5760a0eecfc364b519d30d28ca5f920b23e5d3a1`
**Execution mode:** authorised read-only PostgreSQL (`default_transaction_read_only=on`), no provider calls, no writes, no migrations, no deployment, production observer flag remains OFF.
**Diagnostic script:** existing `sports_edge_tennis.validation.a3_first_run_support_gate`
**Frontier script:** existing `sports_edge_tennis.validation.a3_first_unique_support_frontier`
**Profile execution script:** `ops/validation/a3_profile_historical_execution_20261125.py`
**Evidence JSON:** `docs/research/evidence/a3_profile_historical_execution_20261125.json`

## Exact execution

The DSN was resolved in memory from the linked Railway Postgres service and supplied through `TENNIS_DATABASE_URL`; no credential is printed or committed.

### 1. Reconcile the 2,127-match population

```powershell
python -m sports_edge_tennis.validation.a3_first_unique_support_frontier \
  --start-utc 2026-10-01T00:00:00Z
```

The run used `as_of = 2026-11-22T15:36:43.458510Z`, the original report time. It reproduced the published population exactly:

| Measure | Reproduced | Original |
|---|---:|---:|
| First unique match diagnostics | **2,127** | 2,127 |
| Original A3 predicted | **8** | 8 |
| Original A3 abstained | **2,119** | 2,119 |
| Raw structural soft research candidates | **1,336** | 1,336 |
| Candidate sample SHA256 | `403e984710a99b016234df6d2fad154e1a3cd761cd697ea296ca71c418b0a693` | same |

Category counts also match exactly:

| Category | Count |
|---|---:|
| `RAW_SHARED_BUT_ZERO_ELIGIBLE_EDGES` | 846 |
| `BELOW_NORMAL_NETWORK_SHARED_OR_Q_GATE` | 528 |
| `UNKNOWN_SURFACE` | 355 |
| `NO_RAW_SHARED_OPPONENTS` | 158 |
| `POTENTIALLY_SUPPORTED_BUT_SIGNAL_OR_OTHER_GATE` | 152 |
| `ONE_OR_BOTH_PLAYERS_ZERO_MATCH_HISTORY` | 70 |
| `LATE_OR_NON_FUTURE_EVALUATION` | 9 |
| `ORIGINAL_A3_PREDICTED` | 8 |
| `BOTH_HISTORY_IDENTITIES_UNRESOLVED` | 1 |

Original gate reasons: `INSUFFICIENT_MATCHUP_SUPPORT` 1,931; `UNKNOWN_SURFACE` 179; `WEAK_SIGNAL` 8; `CONFLICTING_DIRECT_NETWORK` 1.

### 2. First-run support gate on current first-run targets

```powershell
python -m sports_edge_tennis.validation.a3_first_run_support_gate \
  --start-utc 2026-10-25T00:00:00Z \
  --end-utc 2026-11-25T00:00:00Z \
  --as-of-utc 2026-11-25T00:00:00Z
```

| Measure | Result |
|---|---:|
| Valid on-time first-run targets | 1,502 |
| Matched same-run diagnostic | 1,502 |
| A3 predicted / abstained | 6 / 1,496 |
| Unsafe late first-run excluded | 11 |
| Dataset SHA256 | `9a47a1f30de91c8aff977d3a294fea187ecbc082c612fc5d127c99ab74351167` |

Classification: `RAW_COMMON_OPPONENTS_ALL_FILTERED_OUT` 785; `COMMON_OPPONENTS_BELOW_MINIMUM_FIVE` 261; `NO_DIRECT_OR_SHARED_OPPONENT_HISTORY` 280; `DIRECT_MATCHES_BUT_LOW_EFFECTIVE_SAMPLE` 99; `NETWORK_THRESHOLD_POSSIBLE_REQUIRES_SIGNAL_CHECK` 43; `FIVE_COMMON_OPPONENTS_BUT_Q_BELOW_EIGHT` 16; `DIRECT_THRESHOLD_POSSIBLE_REQUIRES_SIGNAL_CHECK` 14; `OTHER_INSUFFICIENT_OR_UNCLASSIFIED` 4.

A wider 90-day audit (`2026-08-28`–`2026-11-25`) found 3,813 valid first-run targets, 2,190 matched diagnostics, 16 A3 predictions, 3,797 abstentions and 1,623 targets with no same-run diagnostic. The missing-diagnostic rows are kept explicit; they cannot support a claim of mechanism prevalence.

### 3. Exact source observation and ingestion timestamps

| Measure | Result |
|---|---:|
| `match_versions` rows | 232,541 |
| Versions with `source_observed_at > match_start_at` | 56,559 |
| Versions with `ingested_at > match_start_at` | 56,573 |
| `match_result_observations` COMPLETED | 128,995 |
| RETIRED / WALKOVER / CANCELLED / DEFAULTED | 4,480 / 637 / 544 / 1 |
| Historical result ingestion earliest | 2026-08-31 |
| Historical result ingestion latest | 2026-11-25 |

The 2016–2025 history is retrospective backfill: it was ingested from 31 August 2026 onward, with match-date `as_of` values. It is **not** a genuine pre-match live observation record. The target fixture versions are stricter: only 6 of 4,055 PIT-visible versions were ingested after the run cutoff, and the cohort selection excludes them.

### Historical anchor limitation

The 2022–2025 A3 history is not match-level chronological:

| Season | Versions | `match_start_at` NULL | Distinct `as_of` values |
|---|---:|---:|---:|
| 2022 | 12,725 | 12,725 | 50 |
| 2023 | 13,429 | 13,429 | 55 |
| 2024 | 14,255 | 14,255 | 58 |
| 2025 | 14,058 | 14,058 | 72 |
| 2026 | 114,889 | 0 | 765 |

The 2022–2025 `as_of` values are tournament-week end placeholders (for example `2024-06-09 23:59:59Z` with 532 rows). With `match_start_at` NULL, the production loader's `recency_anchor` falls back to that coarse visibility timestamp. The reconstruction therefore uses weekly anchors, not exact match times, for most of the four-year lookback. This is a data-quality limitation of the stored history, not a modelling choice, and it is reported as AMBER rather than GREEN. It also means a genuinely chronological four-year A3 replay cannot be certified from this dataset alone.

### Target model identity

The target fixtures use live provider player IDs, while the A3 history graph uses resolved model IDs. The execution resolves both:

- 1,608 of 2,597 targets matched the stored `a3_support_diagnostics` model IDs exactly;
- 989 were resolved from the version player IDs through the existing `resolve_model_player_ids` set-based resolver;
- 0 targets remained unresolved.

Using live IDs directly would have produced a disconnected graph and a false zero-coverage result. The fixed execution uses the resolved model IDs on both sides.

### 4. Chronologically and legally eligible records

The executable cohort is defined by the existing first-run definition plus strict version visibility:

- first completed, non-experimental original run per canonical match;
- a `match_version` with `as_of`, `source_observed_at` and `ingested_at` all at or before the run `as_of`;
- tour `ATP_MAIN` or `CHALLENGER`;
- known surface and valid distinct player IDs;
- issue at least 30 minutes before the recorded start;
- latest result `COMPLETED` with `winner_side` 1 or 2.

Result:

| Measure | Count |
|---|---:|
| First-run target rows in window | 3,836 |
| Unknown surface excluded | 889 |
| Not settled/completed excluded | 330 |
| Late or unsafe issue excluded | 20 |
| **Settled eligible targets** | **2,597** |

Cohort composition:

| Month | Tour / surface / draw | Eligible |
|---|---|---:|
| Sep | ATP hard main / qualifying | 67 / 31 |
| Sep | Challenger clay main / qualifying | 196 / 87 |
| Sep | Challenger hard main / qualifying | 226 / 74 |
| Oct | ATP hard main / qualifying | 161 / 45 |
| Oct | Challenger clay main / qualifying | 398 / 88 |
| Oct | Challenger hard main / qualifying | 329 / 88 |
| Nov | ATP hard main | 28 |
| Nov | Challenger clay main / qualifying | 155 / 16 |
| Nov | Challenger hard main / qualifying | 465 / 143 |

Legal status: internal read-only evaluation only. Commercial rights for the stored API-Tennis/Sportradar/Sackmann-derived history are **not** verified, and no dataset fitting was performed. The profiles are fixed research hypotheses, not fitted models.

### 5. Three existing Agent 3 profiles on identical fixtures

The three locked profiles were executed unchanged on the same 2,597 settled fixtures, same run cutoff, same restricted history snapshot and same player orientation:

| Profile | Edge minimum | Cross-surface weight |
|---|---:|---:|
| `original_edge_threshold_diagnostic` | 1.0 | 1.0 |
| `relaxed_edge_050_unweighted` | 0.5 | 1.0 |
| `relaxed_edge_050_surface_soft065` | 0.5 | 0.65 |

The execution reuses the existing `_soft_variant` code and the existing production `predict` control. It is labelled **AMBER reconstructed PIT historical execution**, not a GREEN live-issued record. The stored history is retrospective backfill, so this cannot be presented as an original live forecast.

<!-- PROFILE_RESULTS -->
### Coverage and proper scores

All 2,597 settled targets were scored. The three profiles were run on the same target IDs, same run cutoff, same resolved model IDs and same history snapshot.

| Profile | Predicted | Abstained | Coverage | Accuracy | Brier | Log loss | Brier 95% day-block CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| `original_edge_threshold_diagnostic` | 200 | 2,397 | 7.70% | 0.6450 | 0.23645 | 0.66588 | 0.22820–0.24521 |
| `relaxed_edge_050_surface_soft065` | 511 | 2,086 | 19.68% | 0.5949 | 0.24299 | 0.67906 | 0.23875–0.24835 |
| `relaxed_edge_050_unweighted` | 864 | 1,733 | 33.27% | 0.6215 | 0.23813 | 0.66927 | 0.23326–0.24272 |

Abstention reasons:

| Profile | `INSUFFICIENT_SOFT_EFFECTIVE_SUPPORT` | `WEAK_SHRUNK_SIGNAL` |
|---|---:|---:|
| `original_edge_threshold_diagnostic` | 2,188 | 209 |
| `relaxed_edge_050_surface_soft065` | 1,313 | 773 |
| `relaxed_edge_050_unweighted` | 906 | 827 |

Relaxing the edge threshold increases coverage from 7.7% to 33.3%, but accuracy falls from 0.645 to 0.622 and the soft-065 surface weighting falls further to 0.595. The highest-coverage profile is not the best-scoring one.

### By tour

| Profile | Tour | n | Accuracy | Brier | Log loss |
|---|---|---:|---:|---:|---:|
| edge 1.0 | ATP_MAIN | 82 | 0.7439 | 0.22532 | 0.64347 |
| edge 1.0 | CHALLENGER | 118 | 0.5763 | 0.24418 | 0.68145 |
| edge 0.5 / 1.0 | ATP_MAIN | 172 | 0.7209 | 0.22928 | 0.65155 |
| edge 0.5 / 1.0 | CHALLENGER | 692 | 0.5968 | 0.24033 | 0.67367 |
| edge 0.5 / 0.65 | ATP_MAIN | 135 | 0.7185 | 0.23212 | 0.65726 |
| edge 0.5 / 0.65 | CHALLENGER | 376 | 0.5505 | 0.24689 | 0.68689 |

ATP main-draw performance is materially better than Challenger for every profile. The profiles do not remove the Challenger weakness.

### By surface

| Profile | Surface | n | Accuracy | Brier | Log loss |
|---|---|---:|---:|---:|---:|
| edge 1.0 | HARD | 126 | 0.6587 | 0.23550 | 0.66396 |
| edge 1.0 | CLAY | 74 | 0.6216 | 0.23806 | 0.66914 |
| edge 0.5 / 1.0 | HARD | 562 | 0.6281 | 0.23735 | 0.66773 |
| edge 0.5 / 1.0 | CLAY | 302 | 0.6093 | 0.23956 | 0.67213 |
| edge 0.5 / 0.65 | HARD | 325 | 0.6000 | 0.24273 | 0.67854 |
| edge 0.5 / 0.65 | CLAY | 186 | 0.5860 | 0.24343 | 0.67997 |

### By draw

| Profile | Draw | n | Accuracy | Brier | Log loss |
|---|---|---:|---:|---:|---:|
| edge 1.0 | Main | 186 | 0.6452 | 0.23676 | 0.66653 |
| edge 1.0 | Qualifying | 14 | 0.6429 | 0.23222 | 0.65719 |
| edge 0.5 / 1.0 | Main | 764 | 0.6178 | 0.23850 | 0.67002 |
| edge 0.5 / 1.0 | Qualifying | 100 | 0.6500 | 0.23526 | 0.66354 |
| edge 0.5 / 0.65 | Main | 464 | 0.5948 | 0.24299 | 0.67908 |
| edge 0.5 / 0.65 | Qualifying | 47 | 0.5957 | 0.24290 | 0.67888 |

### Incremental value beyond Agents 1 and 2

Identical intersection: profile predicted and both A1 and A2 predicted with a usable player-1 probability.

| Profile | Intersection n | Profile Brier | A1 Brier | A2 Brier | Δ vs A1 | Δ vs A2 | Profile acc | A1 acc | A2 acc |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| edge 1.0 | 187 | 0.23415 | 0.21589 | 0.22538 | **+0.01826** | **+0.00877** | 0.6631 | 0.6684 | 0.5989 |
| edge 0.5 / 1.0 | 790 | 0.23694 | 0.22244 | 0.23229 | **+0.01451** | **+0.00465** | 0.6316 | 0.6304 | 0.6089 |
| edge 0.5 / 0.65 | 464 | 0.24260 | 0.22556 | 0.23778 | **+0.01705** | **+0.00483** | 0.5948 | 0.6422 | 0.5754 |

Every profile has a **higher Brier and higher log loss than both A1 and A2** on the identical intersection. The relaxed profiles add coverage — 13, 74 and 47 additional predictions beyond the intersection respectively — but the additional coverage does not improve proper scores. A1 remains the best-calibrated arm; A2 is second; the three soft profiles are third, fourth and fifth by Brier.

### Original A3 control fidelity

| Measure | Result |
|---|---:|
| Reconstructed original A3 predicted | 14 |
| Stored original A3 predicted | 15 |
| Both predicted | 14 |
| Status agreement across 2,597 targets | 99.96% |
| Maximum player-1 probability difference where both predicted | 0.004703 |

The reconstruction is very close to the stored original A3, with one status difference and a maximum probability difference below 0.005. This supports the replay's fidelity to the production model on the stored history. It does not turn the reconstruction into a GREEN live-issued record.

### Interpretation

The closest-to-control profile (`original_edge_threshold_diagnostic`) covers 200 targets (7.7%) and scores 0.645 accuracy / 0.2364 Brier. The two relaxed profiles cover 19.7% and 33.3% but score worse. None of the three profiles demonstrates incremental value beyond A1 or A2. The result is a negative finding for the three locked hypotheses, not a promotion candidate. Original Agent 3 remains the control.

## Performance

<!-- PERFORMANCE_RESULTS -->
Per-target in-process latency over 2,597 targets (research execution on the workstation, not production DB/network/lock latency):

| Arm | n | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| Original A3 control | 2,597 | 5.558 | 54.865 | 117.365 |
| `original_edge_threshold_diagnostic` | 2,597 | 0.201 | 1.288 | 3.739 |
| `relaxed_edge_050_surface_soft065` | 2,597 | 0.113 | 0.886 | 3.520 |
| `relaxed_edge_050_unweighted` | 2,597 | 0.128 | 0.993 | 3.011 |

These are pure in-process calculations after the history snapshot is built. They exclude the one-off history load/replay, database round trips and logging. They are not production SLA evidence and do not authorise a live observer pilot.

The full execution wall time was dominated by the one-pass four-year history replay (70,764 observations), not by the per-target profile calls.

## Publication and settlement parity

The execution is offline and read-only. It does not call publication, staking, Manager or settlement code, and it cannot change a published pick or a settlement. The database session is opened with `default_transaction_read_only=on`.

Focused regression tests executed on this branch:

```text
python -m pytest tests/test_a3_soft_common_opponent_ess_shadow.py \
  tests/test_a3_first_run_support_gate.py \
  tests/test_a3_first_unique_support_frontier.py \
  tests/test_a3_soft_live_provisional_capture.py -q
30 passed, 27 skipped in 4.44s

python -m pytest tests/test_observer_live_20261123_evidence.py \
  tests/test_a3_soft_live_provisional_capture.py \
  tests/test_live_orchestration.py -q
30 passed, 27 skipped in 2.06s

python -m pytest tests/test_settlement_postgres.py -q
2 passed, 5 skipped in 1.16s
```

Skips are the existing PostgreSQL-fixture skips where no isolated test DSN is supplied. The branch's PostgreSQL Foundation CI at the verified head passed; the new execution itself uses the authorised production read-only database.

## What this does and does not establish

- It **does** reconcile the 2,127 first-unique diagnostic population exactly.
- It **does** establish a 2,597-match settled, chronologically visible reconstruction cohort.
- It **does** execute the three existing profiles on identical fixtures and report actual coverage, abstentions and proper scores.
- It **does not** establish a GREEN live-issued forecast, commercial data rights, a full official fixture denominator, or a production uplift.
- It **does not** change Agent 3, the Manager, publication, settlement or any production flag.

<!-- VERDICT -->
## Verdict

The empirical result is **negative for all three locked soft profiles**.

- The 2,127 first-unique diagnostic population is reproduced exactly.
- The executable cohort is 2,597 settled, PIT-visible first-run targets.
- The corrected identity mapping and replay reproduce stored original A3 almost exactly (14 vs 15 predictions, 99.96% status agreement, max p1 delta 0.0047).
- The three profiles cover 7.7%, 19.7% and 33.3% of the cohort, but all three have **higher Brier and log loss than A1 and A2** on the identical intersection.
- The closest-to-control profile is the best of the three but still does not beat A1 or A2.
- No production promotion, threshold change, Manager change or observer enablement is justified.

The decisive remaining blocker is data quality, not model code: 2022–2025 history has no match start times and only 50–72 weekly `as_of` anchors per season. The replay is faithful to the existing loader but cannot be certified as a match-level chronological four-year reconstruction. A genuinely chronological, lawfully usable history export is required before any GREEN claim.

## Evidence hashes

```text
docs/research/evidence/a3_first_run_support_diagnostics_20261125.json
SHA256 B0B4E2E2638F223C5C2B2CD8C2081CBCC03A919E30AF1953B809B3DDB16377B4
bytes  88680

docs/research/evidence/a3_profile_historical_execution_20261125.json
SHA256 B16774777FD027A86CF2B5471DFB3899ED204C343BEF4E14B1DCF92225A18D3E
bytes  9082
```
