# Bounded A1/A2 versus A1/A2/A3 main-draw comparison — 25 November 2026

**Branch / PR:** `research/a3-first-issue-live-soft-shadow-20261123` (PR #385)
**Verified starting head:** `647747f26c5e4e95ddb51815a047e43f0ca9fc9b`
**Execution mode:** authorised read-only PostgreSQL (`default_transaction_read_only=on`), no provider calls, no writes to production, no new Agent 3 architecture, no deployment.
**Evidence JSON:** `docs/research/evidence/a3_blend_main_draw_comparison_20261125.json`
**Execution script:** existing `ops/validation/a3_profile_historical_execution_20261125.py`

## Exact execution

The existing script was run unchanged except for the bounded comparison block. The DSN was resolved in memory from the linked Railway Postgres service and passed through `TENNIS_DATABASE_URL`; no credential is printed or committed.

```powershell
$env:TENNIS_DATABASE_URL = <authorised read-only DSN resolved in memory>
python ops/validation/a3_profile_historical_execution_20261125.py \
  --start-utc 2026-09-08T00:00:00Z \
  --end-utc 2026-11-25T00:00:00Z \
  --history-year-start 2022 \
  --out-json docs/research/evidence/a3_blend_main_draw_comparison_20261125.json
```

The run reproduced the existing settled cohort: **2,632** eligible settled targets, with **2,060** main-draw targets. The original A3 control predicted 14; the three soft profiles predicted 201, 514 and 873 respectively.

## Comparison definition

The comparison is deliberately narrow:

- **main draw only** (`qualifying = false`);
- **identical intersection**: rows where A1, A2 and the closest-to-control A3 profile (`original_edge_threshold_diagnostic`) all produced a player-1 probability;
- **identical coverage**: both arms are scored on the same 361 rows;
- **fixed equal weights**: A1/A2 = `(p_A1 + p_A2) / 2`; A1/A2/A3 = `(p_A1 + p_A2 + p_A3) / 3`;
- no weight fitting, threshold search, profile search or extra architecture;
- same binary outcome and same day-block bootstrap seed.

## Result

| Arm | n | Accuracy | Brier | Log loss |
|---|---:|---:|---:|---:|
| A1/A2 equal-weight | 361 | 0.64820 | **0.221233** | **0.632145** |
| A1/A2/A3 equal-weight | 361 | 0.65928 | 0.224396 | 0.640238 |
| A1/A2/A3 − A1/A2 | — | +0.01108 | **+0.003163** | **+0.008093** |

Paired day-block bootstrap for the Brier delta (62 day blocks, 1,000 resamples):

```text
point  +0.003163
95% CI [-0.001256, +0.006978]
```

The interval includes zero. The point estimate is worse (higher Brier and log loss) for the blend that includes A3. Accuracy is marginally higher, but proper scoring rules are the relevant comparison and they do not support an A3 contribution.

## Interpretation

This is **negative/inconclusive**, not a promotion result.

- Adding A3 does not improve Brier or log loss on the identical main-draw intersection.
- The Brier difference is small and its confidence interval crosses zero, so it is not a statistically decisive loss either.
- The accuracy improvement is not supported by the proper scores and is not a reason to promote.
- A3’s coverage is only 7.6% of the settled cohort, so the intersection is a selected subgroup, not the full main-draw population.
- This is a reconstructed historical PIT execution, not a GREEN live-issued forecast. The 2022–2025 history still lacks match-level chronological anchors.
- No additional Agent 3 architecture was developed or tuned.

## Decision

Do not promote A3 into the Manager, publication or production pipeline on this evidence. Do not add another Agent 3 variant on the basis of this comparison. The existing A1/A2 combination remains the supported baseline. A genuinely forward, lawfully captured A3 cohort would be required before any different claim.

## Validation

```text
python -m pytest tests/test_a3_soft_common_opponent_ess_shadow.py \
  tests/test_a3_first_run_support_gate.py \
  tests/test_a3_first_unique_support_frontier.py -q
# existing branch tests; the comparison itself is the execution above.
```

## Evidence hashes

```text
docs/research/evidence/a3_blend_main_draw_comparison_20261125.json
SHA256 0843C0D91B098DD65F59E3CDA86889DAB96AE3D000FEDDBF99392287403DA848
```

