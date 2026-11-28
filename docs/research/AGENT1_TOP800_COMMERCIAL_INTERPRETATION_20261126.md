# Commercial interpretation of the Agent 1 top-800 populations (2026-11-26)

Research-only, read-only. No provider request, purchase, extrapolation or
forecasting-policy change. `report_as_of_utc = 2026-11-26T00:00:00Z`; the
research window is 2026-09-10 → 2026-11-21.

Reproduce:

```
python -m sports_edge_tennis.validation.agent1_top800_bookmaker \
  --as-of-utc 2026-11-26T00:00:00+00:00 \
  --output docs/research/evidence/agent1_top800_bookmaker_real_20261126.json
```

**Coordination note.** No DeepSeek 1 top-800 handoff artifact was present in the
repository or the shared temp directory at execution time. The exact
populations were therefore recomputed from the read-only first-issue Agent 1
ledger with frozen definitions and published match-ID hashes, so any DeepSeek 1
list can be joined and checked exactly rather than trusted by name.

Populations:

- `top800_by_confidence_*`: the 800 highest stored first-issue A1 confidences
  within the window, overall and per tour;
- `latest800_settled_*`: the 800 most recent settled first-issue A1 predictions
  by scheduled start, overall and per tour.

The ATP population has only 578 settled first-issue A1 predictions in the
window, so both orderings return the same 578. This is a population limit, not a
sampling choice.

## 1. Exact populations and model scores

| Population | n | Actual wins | Rate | Mean confidence | Expected wins | Residual | z (indep.) | Brier | IDs SHA256 (prefix) |
|---|---|---|---|---|---|---|---|---|---|
| top800 by confidence, all | 800 | 595 | 0.7438 | 0.8461 | 676.848 | **−81.848** | **−8.10** | 0.20075 | `e855dc2e45fefc09` |
| top800 by confidence, ATP | 578 | 389 | 0.6730 | 0.7119 | 411.475 | −22.475 | −2.15 | 0.21099 | `02dec524f2fe0389` |
| top800 by confidence, Challenger | 800 | 579 | 0.7238 | 0.8202 | 656.148 | **−77.148** | **−7.18** | 0.21112 | `6c6163d0122233e9` |
| latest800 settled, all | 800 | 509 | 0.6363 | 0.6687 | 534.929 | −25.929 | −2.00 | 0.23220 | `660eb6e234b6ed7c` |
| latest800 settled, ATP | 578 | 389 | 0.6730 | 0.7119 | 411.475 | −22.475 | −2.15 | 0.21099 | `02dec524f2fe0389` |
| latest800 settled, Challenger | 800 | 498 | 0.6225 | 0.6669 | 533.554 | −35.554 | −2.74 | 0.23618 | `dcf7634de57149a3` |

The high-confidence top slice is materially overconfident: it wins 74.4% while
its own mean confidence is 84.6% (z ≈ −8.1 under an independence approximation).
The latest-800 slices are less extreme but still below expectation. These are
descriptive historical results from a previously inspected window; the
independence z is optimistic because selections share players and conditions.

## 2. Original-time bookmaker coverage

**Valid original-time two-sided quotes: 0 in every population.** The measured
blocker is not a small sample; it is a sequencing failure.

| Population | Any stored quote | Pre-issue two-sided | Late only | Backfilled only | Unmatched |
|---|---|---|---|---|---|
| top800 confidence, all | 383 | **0** | 383 | 0 | 417 |
| top800 confidence, ATP | 261 | **0** | 261 | 0 | 317 |
| top800 confidence, Challenger | 381 | **0** | 381 | 0 | 419 |
| latest800 settled, all | 244 | **0** | 244 | 0 | 556 |
| latest800 settled, ATP | 261 | **0** | 261 | 0 | 317 |
| latest800 settled, Challenger | 244 | **0** | 244 | 0 | 556 |

Every stored quote is later than the original A1 prediction lock. There are no
backfilled-only or stale-only cases in these populations; the problem is that
the quote is observed/received after the lock, not that old history was
substituted.

Because the priced set is empty, the following are **not estimated**:

- available original-time bookmaker odds;
- de-vig market probabilities;
- bookmaker-expected wins;
- odds distribution (favourite/underdog price quantiles);
- model-versus-market Brier or accuracy.

Reporting zero coverage is the correct result. Extrapolating a tiny or
non-existent valid market sample to 800 selections would manufacture bookmaker
superiority.

## 3. What is measured, and what is not

Measured:

- exact population sizes, match-ID hashes, actual winners, mean confidence,
  expected wins, residuals, Brier and log loss for each population;
- the coverage decomposition (any quote, late-only, backfilled-only, unmatched);
- that zero original-time two-sided quotes exist for all six populations.

Not measured / not claimed:

- market-implied probabilities for these populations;
- bookmaker-expected wins or a market benchmark;
- commercial edge versus a closing or pre-issue line;
- any causal reason for the high-confidence overconfidence beyond the measured
  data-quality and sequencing limits already documented.

## 4. Commercial interpretation

The top-800 A1 slice is not commercially usable as a standalone high-confidence
signal on this evidence. It is overconfident by a wide margin on the
already-inspected window, and no original-time market comparison exists to
establish whether any residual edge survives. The next commercially meaningful
step is the pre-issue acquisition ordering (Phase 3) so a future top-800
population can be compared to a genuine pre-lock market. Until then, the honest
conclusion is: **model overconfidence is measurable; bookmaker superiority is
not measured.**

## 5. Limits

- Historical window previously inspected; not independent validation.
- Top-800 selection by confidence is research ranking, not a deployable cutoff.
- Expected wins and Brier use stored first-issue A1 confidence; no recalibration.
- The independence z ignores shared players/opponents and is optimistic.
- No original-time quote qualifies, so no market metric is reported.
- The research as-of is fixed for reproducibility; production advances hourly.
