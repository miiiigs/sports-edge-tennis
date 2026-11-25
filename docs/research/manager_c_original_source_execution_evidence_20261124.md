# Manager C original-source execution evidence — 2026-11-24

Research-only. No merge, deployment, database write, migration or policy change.
Executed against the authorized production Railway PostgreSQL through an
explicit `REPEATABLE READ READ ONLY` transaction with
`default_transaction_read_only=on`; `SHOW transaction_read_only` returned `on`
and a `CREATE TEMP TABLE` probe was rejected with `ReadOnlySqlTransaction`.

## Exact checkout

- Worktree: `C:\dev\sports-edge-tennis-pr386-20261124`
- Branch: `research/manager-c-forward-feature-ablation-20261124`
- HEAD / `git rev-parse HEAD`: `471eaa0f10d78c3149fa10678fa3bf154df6ed9d`
- PR: #386 (head SHA matches the last verified remote head)
- CI at this head: `postgres-foundation` pass, GitGuardian pass

Focused tests:

```
python -m pytest -q tests/test_manager_c_original_source.py tests/test_manager_c_forward_feature_ablation.py
# 10 passed, 1 skipped
```

## Historical execution (real data)

```
python -m sports_edge_tennis.validation.manager_c_original_source historical \
  --source-sha 471eaa0f10d78c3149fa10678fa3bf154df6ed9d
```

Exit 0. Window `2026-10-15T00:00Z` → `2026-11-21T00:00Z`, as-of
`2026-11-21T03:30Z`.

- `dataset_sha256` = `e7d70fb821d7c69339adfce51bbd7fe35161adda49c595124d1791fef1b56863`
  (matches the registered PR357 baseline exactly)
- `historical_source_hash_matches` = **true**
- `full_source_sha256` = `1c6671317487b8d1b4b091154d51bf4ff681fd3ce34ac7c9bf069bafb0c13b2e`
- `evaluation_match_ids_and_outcomes_sha256` = `ab7673f6281b8abed75ce3bbe2a9d12ee6bfc2fa3d78efb4c590a694e62abc5a`
- `evaluation_first_run_targets` = 656
- exclusions: `UNSAFE_OR_LATE_FIRST_PREMATCH_ISSUE` = 9,
  `UNSAFE_RESULT_GRADED_AS_UNRESOLVED` = 17

## Reproduced baseline and matched selection levels

| Level | Issued | Settled | W | L | P | V | Accuracy | Wilson 95% | Brier | Log loss | Coverage |
|---|---|---|---|---|---|---|---|---|---|---|---|
| narrow | 117 | 107 | 80 | 27 | 1 | 9 | 0.7477 | [0.6578, 0.8204] | 0.18450 | 0.55166 | 0.1784 |
| broad | 275 | 256 | 176 | 80 | 4 | 15 | 0.6875 | [0.6283, 0.7412] | 0.20936 | 0.60693 | 0.4192 |
| marginal (broad−narrow) | 158 | 149 | 96 | 53 | 3 | 6 | 0.6443 | [0.5647, 0.7167] | 0.22721 | 0.64663 | 0.2409 |
| rejected | 381 | 27 | 16 | 11 | 1 | 1 | — | — | — | — | — |

(The rejected group is mostly ungraded: only 29 of 381 records carry a research
grade, of which 27 are W/L.)

Broad agent agreement split: AGREE 257 issued / 239 settled, 163W–76L
(0.6820); DISAGREE 18 issued / 17 settled, 13W–4L (0.7647).

Marginal agent agreement split: AGREE 140 / 132 settled, 83W–49L (0.6288);
DISAGREE 18 / 17 settled, 13W–4L (0.7647).

Exact 117 / 275 / 158 selection counts reproduce the registered cohort, and the
marginal group is exact match-ID nesting (broad−narrow), not a new model.

### ATP / Challenger

| Level | Tour | Issued | Settled | W | L | Accuracy |
|---|---|---|---|---|---|---|
| narrow | ATP_MAIN | 7 | 7 | 5 | 2 | 0.7143 |
| narrow | CHALLENGER | 110 | 100 | 75 | 25 | 0.7500 |
| broad | ATP_MAIN | 13 | 13 | 9 | 4 | 0.6923 |
| broad | CHALLENGER | 262 | 243 | 167 | 76 | 0.6872 |
| marginal | ATP_MAIN | 6 | 6 | 4 | 2 | 0.6667 |
| marginal | CHALLENGER | 152 | 143 | 92 | 51 | 0.6434 |

## Pre-match feature availability (measured)

Broad selection (275 targets):

- `surface_at_issue`: 249 / 275
- `main_draw_verified`: 245 / 275
- `original_agent_probability_pair` (A1+A2 at issue): 275 / 275
- `original_C_probability`: 275 / 275
- `all_original_agent_version_ids`: 275 / 275
- `any_agent_both_rating_samples_visible_unlinked`: **0 / 275**
- `cancelled_in_void`: 7

Agent disagreement is genuinely available from original issue-time predictions:
mean `abs(A1−A2)` = 0.1429 (broad), 0.1189 (narrow), 0.1606 (marginal).

**Structural blocker:** the production `rating_snapshots` table contains **0
rows** (and 0 with `sample_match_count`). Therefore rating sample counts,
rating uncertainty, surface Elo and serve/return depth are not merely unlinked —
they are absent from this database. The census's
`both_rating_sample_counts_visible` flag is structurally 0 and must not be
described as a measured availability rate.

Exact original prediction metadata (run-linked `prediction_id`, lock time,
model/feature/config version) is available for all selected fixtures; that is
metadata, not numeric feature values.

## Eligible prespecified ablations

The pre-registered groups are `calibration`, `disagreement`, `history`,
`uncertainty`.

- `calibration` — eligible, trained (327 labels)
- `disagreement` — eligible, trained (327 labels; A1/A2 pairs present)
- `history` — **ineligible**, 0 training rows (no immutable original-run
  `min_player_prior_match_count`)
- `uncertainty` — **ineligible**, 0 training rows (no immutable original-run
  `max_player_rating_uncertainty`)

No historical ablation outcome comparison was produced. Evaluating the learned
weights on Nov 4–20 would re-use the already-inspected evaluation period, which
the task and the adapter both forbid. The genuine comparison is the frozen
prospective one below.

## Prospective freeze — registered and independently timestamped

```
python -m sports_edge_tennis.validation.manager_c_original_source freeze \
  --source-sha 471eaa0f10d78c3149fa10678fa3bf154df6ed9d \
  --boundary-utc 2026-11-25T00:00:00Z
```

- freeze_at: `2026-11-25T00:00:00Z` (future at execution)
- evaluation_end: `2026-12-23T00:00:00Z`
- settlement_as_of: `2026-12-30T00:00:00Z`
- train targets 822, validation targets 535 (main-draw verified only)
- `manifest_sha256` = `1d9f8748b76c9569071f19ee939d732d400cacc638c06247fc714cdbd8c6f32f`
- `envelope_sha256` (field) = `c627c67508e914b2f8891d204ec6833e19489f5ce7a1fb202279e2e0e4e54ff1`
- private envelope file SHA256 = `bb54892acf5b661f72eab47c7abdcd5d78e6868520071476f308364283c8c74c`
- `history_uncertainty_status` = `NO_VERIFIED_ORIGINAL_NUMERIC_SNAPSHOT`

Independent RFC 3161 timestamp from `http://timestamp.digicert.com`:

- `gen_time_utc` = `2026-11-24T09:11:36Z` (before the boundary)
- imprint SHA256 = `bb54892acf5b661f72eab47c7abdcd5d78e6868520071476f308364283c8c74c`
- token SHA256 = `1d27027d824ee1c0ec9513c08019730bb737dfe67ec949cd9189d5c8572278ab`
- signature verified locally against the embedded responder certificate
  (`CN=DigiCert SHA256 RSA4096 Timestamp Responder 2026 1`); root and
  intermediate certificates correctly fail. Root-chain trust is not asserted.
- archived: `docs/research/manager_c_frozen_envelope_20261125.tsr` and
  `docs/research/manager_c_frozen_envelope_20261125_attestation.json`
  (the timestamped subject is a SHA-256 hash; no fixture IDs are in these files)

The private envelope (contains match IDs) was kept outside the repository. Only
hashes and the signed token are archived. The freeze boundary was met with
about 15 hours to spare; do not evaluate before `2026-12-30T00:00Z`.

## Interpretation limits

- The baseline is descriptive on a previously inspected period, not new
  evidence of forward performance.
- No numeric original feature values are proven; only original probabilities,
  metadata and agent disagreement are.
- No promotion, no threshold selection, no production policy change.
