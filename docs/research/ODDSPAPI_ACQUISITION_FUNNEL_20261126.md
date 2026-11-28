# Complete OddsPapi acquisition funnel reconciliation (2026-11-26)

Research-only, read-only. No provider request, purchase, deployment, write or
matching-threshold change. The frozen cohort is the 1,342 first-issued original
Model Picks at the 2026-11-22T15:28:44Z audit snapshot, reproduced exactly.

Reproduce:

```
python ops/validation/odds_acquisition_funnel_probe.py --fast \
  --output docs/research/evidence/odds_acquisition_funnel_real_20261126.json
python ops/validation/oddspapi_capture_evidence_probe.py
```

Companion evidence:

- `docs/research/evidence/odds_acquisition_strict_probe_20261126.json`
  (per-fixture strict/counterfactual audit; aggregate only);
- `docs/research/evidence/oddspapi_capture_timing_real_20261126.json`
  (latest 3,905-fixture identity/price timing);
- `docs/research/evidence/original_preissue_odds_gap_20261122.json`
  (the earlier 23-pair audit).

## 1. Exact cohort-specific counts

Cohort: 1,342 original predictions (954 `A1_A2_WIDE_SPREAD`, 388
`MANAGER_C_CONSENSUS`).

| Stage | Count | Definition |
|---|---|---|
| Original predictions | **1,342** | exact frozen first-issued cohort |
| Provider fixtures discovered | **214** current / **1** at issue | persisted `ODDSPAPI` match link; raw observation exists for 213 |
| Independently verified identities | **0** | requires independent `ODDSPAPI` tournament mapping + edition window + player/start checks |
| Valid match-winner markets | **676** | at least one stored two-sided MATCH_WINNER market, any time |
| Both participants' prices | **676** | PLAYER_1 and PLAYER_2 present for the same book |
| Quotes observed before first issue | **23 fixtures / 688 quote rows** | `source_observed_at <= first issue` |
| Quotes received before first issue | **23 fixtures / 678 quote rows** | `ingested_at <= first issue` |
| Late-ingested observations | **5 fixtures / 10 rows** | observed before issue, ingested after issue |
| Historical backfills / stale observations | **14 fixtures / 218 rows** | observed more than six hours before issue |
| Final qualifying comparison pairs | **0 strict; 22 freshness-strict; 23 legacy** | see sections 2–3 |

The first-issue cutoff is `max(prediction as_of, prediction finalized_at)`, the
conservative original publication moment. Using the model `as_of` alone would
silently promote work that was finalized later.

## 2. Reconciliation of 23, 22 and 0

These are three different gates, not three measurements of the same thing:

| Count | Gate | Meaning |
|---|---|---|
| **23** | legacy: live two-sided quote, observed/received before issue, no six-hour freshness bound, **no independent tournament mapping** | the earlier audit's "qualifying pairs" |
| **22** | add six-hour freshness | one of the 23 is stale under the stricter window |
| **0** | add independent `ODDSPAPI` tournament mapping + edition end date | the strict independent-evidence gate |
| **18** | previously documented counterfactual: mapping + start alignment assumed | still a labelled counterfactual, never recovered coverage |

The 23 legacy pairs are **not OddsPapi acquisitions**. Their latest qualifying
source distribution is:

| Source | Bookmaker | Fixtures |
|---|---|---|
| OFFERED | SBO | 10 |
| OFFERED | 1XBET | 4 |
| OFFERED | PNCL | 3 |
| SHARP | PINNACLE | 2 |
| OFFERED | BETANO | 2 |
| OFFERED | BET365 | 2 |

Only **one** of the 23 currently has an OddsPapi identity and raw price; at
issue exactly one did. The earlier 23-pair figure therefore cannot be used as
OddsPapi coverage, and the latest 505-fixture OddsPapi audit cannot be used to
recover it.

## 3. Latest identity and price timing

Across 3,905 fixtures with original predictions:

| Check | Result |
|---|---|
| With an OddsPapi identity | **509** |
| Identity before prediction `as_of` | **0** |
| Identity before prediction lock | **0** |
| Median identity lag | **39,557 s (~11.0 h)** |
| With a raw OddsPapi price | **505** |
| Price before prediction `as_of` | **0** |
| Median price lag | **39,612 s (~11.0 h)** |

On the frozen 1,342 cohort the same pattern is sharper: 214 identities exist
now, but only **1** existed at issue; 213 raw price fixtures exist now, but only
**1** was received/observed at issue. The rest are later acquisitions. The older
"500 identity / 496 price" figures have advanced to 509/505 because the live
cron continued; the conclusion is unchanged.

## 4. Raw observation survival and normalization

- **10,939** raw OddsPapi observations are stored.
- **0** raw observations currently lack a persisted OddsPapi fixture link.
- **5,119** raw observations cover **213** of the 1,342 cohort fixtures.
- **0** normalized `odds_snapshots` rows have `source_system='ODDSPAPI'`.
- The strict audit reconstructs market rows from raw observations; it does not
  find normalized OddsPapi prices.
- Ingestion is fast: p50 0.61 s, p90 1.85 s, max 46.47 s. The failure is
  **ordering**, not write latency.
- Only 16 raw observations use `PROVIDER_TIME`; the rest use `RECEIVE_TIME`, so
  a "provider said it earlier" claim cannot be reconstructed for most rows.

Raw observations survive failed/late fixture matching: they are in the raw
table, but they do not become canonical prices and they cannot be promoted to
pre-issue coverage. Absence of a normalized ODDSPAPI quote is **not** proof that
the provider had no price.

## 5. Independent tournament mappings and edition windows

- `tournament_source_ids` for `ODDSPAPI`: **0**.
- Cached OddsPapi identities: **136 rows**, 27 distinct tournaments.
- Cached canonical editions: **27**, and **27 / 27 have `end_date IS NULL`**.
- Whole database: **2,579** editions, **360** missing `end_date`.
- The cache's own `oddspapi_tournament_id` is deliberately not treated as
  independent proof.
- No mapping was created or inferred.

The strict identity gate therefore cannot pass for any fixture. This is the
measured blocker, not a threshold choice.

## 6. Provider versus canonical start semantics

| Corpus | Matched | Min | Median | Max | Inside ±30 min |
|---|---|---|---|---|---|
| Frozen 1,342 cohort | 213 | −60 min | **−10 min** | +55 min | 186 |
| Latest full corpus | 505 | −60 min | **−10 min** | +60 min | 434 |

The earlier 8-fixture −40/−50/−60 pattern is the tail, not the typical case.
The mismatch is real, bounded at ±60 minutes in this corpus, and is not a fixed
offset. A single 30-minute rule would reject ~13–14% of matched fixtures. No
tolerance was loosened.

Three different tolerances exist in code for the same field: 60 minutes in
discovery reconciliation, 30 minutes in the evidence audit/cache invalidation,
and 120 minutes in the reconciler default. They encode different semantics
(provider session time versus canonical match start); resolving them needs an
approved scheduling-semantics study plus authoritative start data.

## 7. Safe pre-issue acquisition ordering

New module: `src/sports_edge_tennis/live/preissue_acquisition.py`.

It provides:

- deterministic ordering by earliest safe deadline
  `min(first_issue, scheduled_start − 30 min)`;
- entitlement and monthly-allowance gating before any work;
- the existing per-cycle budget planner and exact request plan;
- injection of identity and price fetchers (no transport import, no network);
- a hard attempt cap and a wall-clock wait budget;
- terminal status for every fixture (`CAPTURED`, `TOO_LATE`,
  `IDENTITY_UNAVAILABLE`, `PRICE_UNAVAILABLE`, `BUDGET_DEFERRED`, `TIMEOUT`,
  `PROVIDER_ERROR`, …);
- no exception escapes to the forecaster: a missing price never blocks the
  original forecast.

Tests: `tests/test_preissue_acquisition.py` — 12 passed, covering ordering,
too-late/issued/horizon cases, missing entitlement, exhausted monthly allowance,
per-cycle deferral, captured prices, identity/price failures, provider errors,
attempt-cap exhaustion and the wait budget.

The live cycle was **not** reordered. Activating this path is a runtime
behaviour change that still requires approval and a provider-quota plan. The
implementation is ready for that decision and makes no provider call in tests or
in this session.

## 8. Provider-call and spend discipline

- Provider calls made by this work: **0**.
- Purchases: **0**.
- The production live cron advanced the ledger independently: 2,114 rows,
  latest 2026-11-26T04:03:43Z, endpoint totals `oddspapi fixtures` 852 and
  `oddspapi tournament odds` 1,262. These are not this session's calls.
- No matching threshold, mapping, cache row or normalized price was changed.

## 9. Remaining blockers

1. Identity and prices are still acquired after first issue; the ordering
   implementation is not activated.
2. Independent OddsPapi tournament mappings: 0; edition end dates missing for
   27/27 cached tournaments.
3. Start-time semantics unresolved (three tolerances; ±60-minute drift).
4. No normalized OddsPapi prices; raw observations survive but do not qualify.
5. Historical cache state before migration 0024 cannot be reconstructed.
6. The 23 legacy pairs are non-OddsPapi quotes and cannot be relabelled as
   OddsPapi coverage.

## 10. Conclusion

The 1,342 → 23 legacy pairs → 0 strict pairs reconciliation is complete and
internally consistent. The 23 pairs are mostly offered-bookmaker quotes already
present in the canonical ledger, not OddsPapi. OddsPapi adds 505 raw fixtures
but zero pre-issue identities or prices; every raw observation is later than the
first issue. Raw observations survive in the raw table and survive failed
matching, but normalization and ordering fail. The pre-issue ordering primitive
is implemented and tested, but not activated. No billable call, purchase or
forecasting-policy change was made.
