# Fixed Manager C identity comparison: A versus B (2026-11-26)

Research-only, read-only. No retrain, refreeze, new threshold, deployment,
provider request, purchase or forecasting-policy change. The registered
prospective Manager evaluation and settlement boundary are untouched and were
not scored. This is Brian's requested fixed experiment, not the previously
completed broader 3,271-fixture production Manager experiment.

- **A:** stored original Agent 1 + stored Agent 2/3 + existing Manager C rule.
- **B:** identity-corrected Agent 1 + the identical stored Agent 2/3 + the
  identical Manager C rule.

Both arms use the same 656 eligible evaluation fixtures (start
2026-11-04 → 2026-11-21), original forecast cutoffs, frozen 20%/40% thresholds
(0.7070145 / 0.62133), registered uncalibrated-mean policy and settlement.
Only Agent 1's history namespace changes.

Reproduce:

```
python -m sports_edge_tennis.validation.manager_c_identity_comparison \
  --output docs/research/evidence/manager_c_identity_comparison_real_20261126.json
```

## 1. Reproduction and identity mapping

Agent 1 was replayed locally with the production code. With the corrected
target namespace:

| Check | Result |
|---|---|
| A1 targets replayed | 656 / 656 (both arms) |
| Exact reproduction of stored A1 (1e-6) | **589 / 656 = 89.8%** |
| Within 0.005 of stored A1 | **599 / 656 = 91.3%** |
| Median absolute difference | **0.000000** |
| A-arm policy reproduction from stored A1/A2/A3 | **656 / 656 exact, 0 mismatches** |
| Target players resolved by the bridge | 416 / 484 |
| Mapping invariant across sampled cutoffs | true |

The 10.2% non-exact replay cases are harness error, not identity effects. The
sensitivity arm **A′ (unbridged replay)** versus B isolates the identity change
from that error; it is reported alongside A versus B. A is the exact original
Manager C candidate, as the zero policy mismatches confirm.

## 2. Frozen-threshold results (all tours)

| Arm | Cohort | Selected | Settled | W–L | Accuracy | Expected wins | Residual | Mean p | Brier | Log loss | Coverage |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | broad | 275 | 256 | 176–80 | 0.6875 | 181.831 | −5.831 | 0.7103 | 0.20936 | 0.60693 | 41.92% |
| B | broad | 227 | 211 | 150–61 | 0.7109 | 150.431 | −0.431 | 0.7129 | **0.19385** | **0.57079** | 34.60% |
| A | narrow | 117 | 107 | 80–27 | 0.7477 | 83.274 | −3.274 | 0.7783 | 0.18450 | 0.55166 | 17.84% |
| B | narrow | 98 | 90 | 74–16 | 0.8222 | 70.147 | +3.853 | 0.7794 | **0.14450** | **0.46007** | 14.94% |
| A | marginal | 158 | 149 | 96–53 | 0.6443 | 98.556 | −2.556 | 0.6615 | **0.22721** | **0.64663** | 24.09% |
| B | marginal | 129 | 121 | 76–45 | 0.6281 | 80.284 | −4.284 | 0.6635 | 0.23056 | 0.65314 | 19.66% |

Overall, B improves broad accuracy by +2.34 pp and Brier by 0.0155, but selects
48 fewer fixtures (−7.3 pp coverage). It is not a like-for-like coverage
comparison, which is why the equal-count results below are also reported.

## 3. Marginal Challenger focus

| Arm | Selected | Settled | W–L | Accuracy | Expected wins | Residual | Brier | Log loss | Coverage |
|---|---|---|---|---|---|---|---|---|---|
| A | 152 | 143 | 92–51 | **0.6434** | **94.559** | **−2.559** | **0.22759** | **0.64740** | 26.12% |
| B | 123 | 115 | 71–44 | 0.6174 | 76.372 | −5.372 | 0.23325 | 0.65864 | 21.13% |

On the marginal Challenger population the identity correction is **not** an
improvement: accuracy is −2.6 pp, the residual is 2.8 wins more negative, and
Brier/log loss are slightly worse. The paired 95% intervals (below) span zero,
so this is not a reliable degradation either. It is a genuine null on the
marginal cohort while the broader/narrow cohorts improve.

## 4. Selection changes and corrected predictions

| Cohort | A selected | B selected | Shared | Removed | Newly selected | Side flips | Corrected | Newly incorrect |
|---|---|---|---|---|---|---|---|---|
| Broad (all) | 275 | 227 | 198 | 77 | 29 | 0 | **1** | **0** |
| Marginal (all) | 158 | 129 | 68 | 90 | 61 | 1 | **1** | **0** |
| Broad (Challenger) | 262 | 214 | 187 | 75 | 27 | 0 | **1** | **0** |
| Marginal (Challenger) | 152 | 123 | 65 | 87 | 58 | 1 | **1** | **0** |

Exactly one shared fixture is corrected, and no shared fixture becomes newly
incorrect. The one marginal side flip is that corrected fixture. The large
membership churn in marginal is expected: changing Agent 1 moves confidence
across the frozen 0.62133 boundary, and the marginal definition is
`broad MINUS narrow` in each arm.

## 5. Equal-count and frozen-threshold coverage

Frozen thresholds are the deployable comparison. Equal-count top-K is
descriptive only.

| Population | K | A accuracy | A Brier | B accuracy | B Brier |
|---|---|---|---|---|---|
| All, 20% | 132 | 0.7377 (90/122) | 0.18955 | **0.8099 (98/121)** | **0.15336** |
| All, 40% | 263 | 0.6939 (170/245) | 0.20735 | **0.7037 (171/243)** | **0.19833** |
| Challenger, 20% | 117 | 0.7290 (78/107) | 0.19417 | **0.8396 (89/106)** | **0.14128** |
| Challenger, 40% | 233 | 0.7023 (151/215) | 0.20522 | 0.7009 (150/214) | **0.19753** |

At equal coverage B is consistently better on Brier, and materially better at
the 20% top slice. At 40% Challenger the accuracy is effectively tied while
Brier still favors B. B's frozen-threshold coverage reduction is therefore not
the sole source of its overall advantage.

## 6. Calibration and paired uncertainty

Paired player-clustered bootstrap, 4,000 resamples. Brier improvement is
A − B (positive = A worse); accuracy improvement is A − B (negative = B better).

| Population | Paired settled | Brier improvement 95% | Accuracy improvement 95% | Residual improvement 95% |
|---|---|---|---|---|
| Broad, match cluster | 206 | **+0.00283 to +0.01764** | −0.01456 to 0.00000 | −0.03318 to −0.00929 |
| Broad, favourite cluster | — | +0.00284 to +0.01787 | −0.01546 to 0.00000 | — |
| Marginal, match cluster | 103 | −0.00297 to +0.01424 | −0.02913 to 0.00000 | −0.03588 to +0.00544 |
| Marginal, favourite cluster | — | −0.00350 to +0.01467 | −0.03030 to 0.00000 | — |
| A′ vs B (replay-only), broad | 207 | +0.00314 to +0.01786 | −0.01449 to 0.00000 | −0.03191 to −0.00903 |

The broad Brier improvement excludes zero in both clustering schemes. The
marginal Brier interval spans zero, matching the null in section 3. The
replay-only A′ versus B comparison reproduces the same broad conclusion, so the
result is not an artefact of harness error.

## 7. ATP main / Challenger breakdown

- **ATP broad:** A 13 settled, 9–4, expected 9.555, residual −0.555, Brier
  0.19407. B 13 settled, 10–3, expected 9.341, residual +0.659, Brier 0.18255.
  n = 13; no strong inference.
- **Challenger broad:** A 243 settled, 167–76, expected 172.276, residual
  −5.276, Brier 0.21018. B 198 settled, 140–58, expected 141.090, residual
  −1.090, Brier 0.19459.
- **Challenger narrow:** A 100 settled, 75–25, expected 77.717, Brier 0.18528.
  B 83 settled, 69–14, expected 64.718, Brier 0.14102.
- **Challenger marginal:** as section 3.

The broad/narrow improvement is a Challenger effect; the ATP sample is too small
to claim one.

## 8. Bookmaker comparison

**No original-time two-sided quote qualifies for any arm.** Eligible fixtures:
0/656. The strict rule required both sides, observed/as-of/ingested at or before
the original A1 lock, within six hours, same source, five-minute span and a sane
de-vig total. All stored quotes are later or backfilled relative to first issue.
No market-versus-model comparison is manufactured; the measured blocker is
reported instead.

## 9. Limits

- The November window was previously inspected; not independent forward
  validation.
- B requires a local replay; 10.2% of A1 probabilities are not exactly
  reproduced. A′ versus B isolates the identity change.
- Only A1 changes. A2/A3 stored inputs, Manager rule, cutoffs, thresholds,
  calibration and settlement are identical.
- Identity resolution is invariant across the sampled cutoffs but remains an
  alias-uniqueness bridge, not a verified provider census.
- Marginal membership changes with the arm because the definition depends on
  each arm's broad/narrow selection.
- The registered prospective evaluation remains the only independent
  comparison; it was not retrained, refrozen or prematurely scored.

## 10. Conclusion

Identity-correcting Agent 1 materially improves the broad and narrow Manager C
probability scores and, at equal coverage, the top-20% and top-40% slices. It
does not improve the marginal Challenger cohort, where the paired interval spans
zero. The correction changes selection membership substantially and fixes one
winner prediction without breaking any shared correct prediction. The
bookmaker comparison remains blocked by the absence of original-time two-sided
quotes. These are descriptive historical results, not a new policy, threshold or
forecasting rule.
