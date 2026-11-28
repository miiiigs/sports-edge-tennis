# Manager C feature-snapshot investigation and minimal implementation (2026-11-25)

Research-only. No migration applied, no production write, no retrain, no change
to the registered 2026-11-25 freeze. All database reads were
`REPEATABLE READ READ ONLY` with `default_transaction_read_only=on`.

## 1. Frozen manifest verification (unchanged)

Verified against the private envelope and the archived RFC 3161 token:

| Check | Result |
|---|---|
| `envelope_sha256` field recomputes | true |
| `manifest_sha256` recomputes | true |
| TSR imprint = envelope file SHA256 | true (`bb54892a…`) |
| token signature (responder cert) | true |
| token genTime | `2026-11-24T09:11:36Z` (before `2026-11-25T00:00:00Z`) |
| CA + responder chain signatures | true |
| certificates valid at genTime | true |
| timeStamping EKU on CA/responder | true (`1.3.6.1.5.5.7.3.8`) |
| frozen policy matches code (`VERSION`, `GROUPS`, `TARGETS`) | true |
| manifest train / validation | 822 / 535 |
| evaluation / settlement | 2026-12-23 / 2026-12-30 |

The token's root is a cross-signed DigiCert Trusted Root G4 whose issuer is not
embedded, so chain signatures verify within the supplied set but the final
trust anchor comes from the verifier's trust store. This is unchanged evidence;
the experiment was not retrained or re-scored. Full record:
`docs/research/manager_c_frozen_envelope_20261125_verification.json`.

## 2. Why `rating_snapshots` is empty

`rating_snapshots` is defined in `0001_tennis_data_foundation` and protected by
the append-only trigger from `0005`, but **no runtime code writes it**. A
repository-wide search finds inserts only in tests
(`tests/test_postgres_integration.py`). Production count is 0 rows, 0 with
`sample_match_count`.

The live/replay path does not read that table for Agent 1. Agent 1 computes
ratings in memory with `PlayerRatingState`:

- `overall_rating`, `overall_match_count`
- `surface_ratings[surface]`, `surface_match_counts[surface]`
- `PredictionDiagnostics` already carries
  `player_1/2_overall_matches`, `player_1/2_surface_matches`,
  `cold_start` and `surface_cold_start` flags.

So the census's `both_rating_sample_counts_visible = 0` is structural, not a
coverage measurement. Nothing is "unlinked" that actually exists; the numeric
values exist only transiently in the prediction process.

### What is genuinely available at prediction time

| Feature | Available? | Source |
|---|---|---|
| overall Elo rating per player | yes | `PlayerRatingState.overall_rating` |
| overall history count | yes | `PlayerRatingState.overall_match_count` |
| surface Elo rating | yes, when surface known | `surface_ratings` |
| surface history count | yes, when surface known | `surface_match_counts` |
| canonical player ids | yes | request / states |
| main-draw flag | yes | `qualifying_flag is False` |
| rating uncertainty | **no** | Agent 1 never computes an uncertainty or standard error |
| serve/return depth | **no** | not an Agent 1 input |

`rating_uncertainty` must therefore be stored as NULL. Deriving it from sample
counts would fabricate a feature that the model never consumed.

## 3. Minimal immutable, run-linked implementation

- Migration `db/migrations/0023_prediction_feature_snapshots.up.sql`:
  one row per `prediction_id` (UNIQUE), linked to
  `prediction_runs` and `matches`, append-only trigger, explicit
  `identity_complete` / `surface_known` / `main_draw_verified` flags.
- `src/sports_edge_tennis/data/feature_snapshots.py`:
  `build_agent1_feature_snapshot()` reads the exact `PlayerRatingState` pair
  used for the prediction, and `persist_feature_snapshot()` writes one row and
  returns its id. `load_feature_snapshot()` reads it back for audits.
- `identity_complete` means both players had at least one prior match in the
  shared pool (`overall_match_count > 0`); it is a history-completeness flag,
  not a validated metric claim.
- Uncertainty is accepted only when a producer supplies both a value and a
  named source; otherwise it is NULL.

**Integration point (not yet wired):** in `replay/engine.py::_adapt_agent_results`
the exact `a1_result` and `run.agent1_replay.player_states[player_id]` are in
scope. The writer must be called in the same transaction that returns
`prediction_id` from `persist_predictive_agent_result`, so a prediction and its
snapshot either both commit or neither does. The live batch path has the same
state through its Agent 1 request/replay objects.

Tests: `tests/test_prediction_feature_snapshots.py` — 12 passed. Migration is
not applied; no database DDL was executed.

## 4. Actual vs AI-expected wins (descriptive only)

Original settled marginal **Challenger** population (broad minus narrow, exact
match IDs), selected-side confidence:

| Metric | Value |
|---|---|
| settled records | 143 |
| actual wins | **92** |
| expected wins (Σ selected p) | **94.559** |
| residual (actual − expected) | −2.559 |
| mean selected probability | 0.6613 |
| actual rate | 0.6434 |
| binomial SD if calibrated | 5.653 |
| z | −0.453 |
| Brier (selected) | 0.22759 |
| log loss (selected) | 0.64740 |

Buckets: 0.60–0.70 → 136 picks, 89 expected, 89 actual; 0.70–0.80 → 7 picks,
4.92 expected, 3 actual. For context, marginal ATP_MAIN: 6 picks, 4 actual vs
4.00 expected.

Interpretation: within this already-inspected period the marginal Challenger
population is descriptively well calibrated (z ≈ −0.45); it is **not** an
independent test and was not used to tune anything. The registered prospective
evaluation remains the only independent comparison.

## 5. Remaining blockers

- No runtime writer exists yet; wiring requires a production change and an
  applied migration, both awaiting approval.
- Even after wiring, historical predictions cannot be backfilled truthfully —
  the values did not exist. Forward snapshots only.
- Rating uncertainty and serve/return depth remain unavailable unless a
  producer computes them; they must stay NULL.
