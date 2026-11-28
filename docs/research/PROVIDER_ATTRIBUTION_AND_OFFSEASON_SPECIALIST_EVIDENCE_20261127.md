# Original-time bookmaker acquisition and specialist-agent evidence (2026-11-27)

Research and engineering record for PR #392. Read-only production access; no
provider request, purchase, deployment, migration or Manager-policy change. The
remote branch was reconciled first: `origin/integration/offseason-evidence-foundation-20261125`
was still at the verified checkpoint `6db5913986a9173bae56971ed30275d80912b7f7`.
The live cron advanced the stored corpus during the session, so counts below are
higher than the checkpoint (11,087 raw / 21,976 normalized) and are stamped with
their measurement time.

Reproduce:

```
python ops/validation/provider_reconciliation_probe_20261127.py \
  --output docs/research/evidence/provider_reconciliation_real_20261127.json
python -m sports_edge_tennis.validation.acquisition_delay_decomposition \
  --output docs/research/evidence/acquisition_delay_real_20261127.json
python ops/validation/manager_c_cohort_probe_20261127.py \
  --output docs/research/evidence/manager_c_cohort_reconciliation_real_20261127.json
python ops/validation/market_benchmark_audit_20261127.py \
  --output docs/research/evidence/market_benchmark_real_20261127.json
```

## 1. Permanent provider attribution

### Design decision: no new migration

The immutable schema already preserves every attribution fact:

| Requirement | Preserved by |
|---|---|
| upstream API provider | `raw_odds_observations.provider_code` |
| provider fixture identifier | `raw_odds_observations.provider_event_id` |
| actual bookmaker | `provider_bookmaker_mappings.provider_bookmaker_key` + `bookmaker_code` |
| canonical match/participants | `odds_snapshots.match_id`, PIT `match_versions` |
| raw acquisition reference | `odds_snapshots.raw_odds_observation_id` |
| normalized snapshot reference | `odds_snapshots.odds_snapshot_id` |
| historical classification | derived from raw payload shape (`bookmakers` vs `bookmakerOdds`) |
| benchmark qualification | computed per prediction cutoff, never intrinsic to a quote |

Copying these values into another table would create a second source of truth.
`src/sports_edge_tennis/odds/attribution.py` exposes them as a canonical read
model. Benchmark qualification is deliberately not stored per quote: the same
quote can qualify against one prediction cutoff and not another.

**Migration coordination.** PR #394 owns `0025_identity_provenance_v2` and
`0026_forward_evidence_outbox`, and its 0025 comment explicitly states that
0023/0024 are owned by PR #392. No migration version was allocated here.
Allocating 0027 on this branch would either conflict with #394 or leave a
non-contiguous chain; no DDL is needed for this read model.

### Measured reconciliation (2026-11-27T03:37Z)

| Provider | Raw responses | Extracted winner observations | Normalized quotes | Normalized fixtures | Two-sided markets | Original-time comparisons |
|---|---:|---:|---:|---:|---:|---:|
| ODDSPAPI | 11,352 | 11,352 | **22,500** | 522 | 10,865 | **0** |
| API_TENNIS | 22,515 | 22,507 | 101,172 | 1,327 | 50,586 | **0** |
| PINNAPI | 43,322 | 0 (board snapshots) | 20,110 | 875 | 10,055 | **0** |

ODDSPAPI rejection reasons: 11,250 normalized, 77 provider-start-passed,
25 other. API-Tennis: 19,475 normalized, 2,036 canonical-start-passed (live
polls are not prematch evidence), 930 no common bookmaker across sides, 6 no
`Home/Away` market, 68 other. The zero-normalization diagnosis is disproven,
and a real-format OddsPapi insert test now verifies inclusion in the commercial
coverage report.

Additional attribution facts:

- ODDSPAPI normalized bookmakers: 4 (`PINNACLE`, `BET365`, `1XBET`, `UNIBET`);
  `PINNACLE` carries 7,416 quotes across 519 matches in this snapshot.
- `provider_bookmaker_mappings` retains both identities, for example
  upstream `pinnacle+30` → canonical `PINNACLE`, class `SHARP`, while
  API-Tennis `Pncl` maps to `PNCL`.
- `ODDSPAPI` has 529 `match_source_ids` rows and 525 raw fixtures; 4 mappings
  have no stored raw price.

## 2. Acquisition-delay decomposition

Measured on all 522 ODDSPAPI fixtures that have both a first-issued Agent 1
prediction and a stored price.

| Stage | p50 | p90 |
|---|---:|---:|
| Issue → first stored price | **39,591.6 s (10.997 h)** | 136,723.9 s |
| Batch-schedule wait (issue → acquiring run start) | **39,554.5 s (10.987 h)** | 136,686.9 s |
| Within-batch identity wait | 0.0 s | 35.6 s |
| Post-identity request/provider wait | 40.6 s | 51.1 s |
| Observation → persistence | 1.16 s | 2.64 s |
| Residual | 0.0 s | 0.0 s |

**99.89% of the median lag is batch scheduling**, not identity resolution,
provider latency or database writes. The acquiring run begins a median 10.99
hours after the first prediction was issued. Requests and persistence together
account for about 42 seconds.

Supporting facts:

- 49 of 522 fixtures had their ODDSPAPI identity cache row written *after* the
  price; that is late identity recording, not a reason the price was late.
- 141 fixtures have a cache discovery clock; its median delta from issue is
  46,820 s, but it overlaps the batch wait rather than adding to it.
- 94 of 522 first stored prices were observed at or after the canonical
  scheduled start. Those are late acquisitions and cannot be pre-match evidence.
- Provider fixture `updatedAt`, `trueStartTime`, `statusId` and a Betradar
  external id exist for all 525 fixtures. Actual competitive start is not stored
  in the schema and is reported as `NOT_STORED`, not inferred.
- Price-change age is reported separately from observation age. A price
  unchanged for ten hours is not stale when a fresh observation reconfirms its
  availability; the test suite locks that distinction.

The honest conclusion: the 10-11 hour delay is a **request-scheduling
decision** (when the live batch runs and when a fixture enters qualification),
not a normalizer, mapping or write problem. Changing it needs an approved
provider-quota and scheduling plan.

## 3. Fixture metadata and market catalogue

- Versioned definition: sport `12`, market `121`, outcomes `121`/`122`, ordered
  participant-1 then participant-2 (`ODDSPAPI_TENNIS_CATALOGUE_V1`).
- 525/525 latest fixture payloads produced **zero** critical catalogue alerts.
- Historical `bookmakers` payloads with list-valued players are classified as
  latest-at-cutoff reconstructions and are never treated as live prices.
- Identity cache: 144 rows, 27 tournaments, 144/144 `DIRECT` orientation and
  `EXACT` resolution.
- Tournament editions: 27 cached OddsPapi editions and **27/27 have
  `end_date IS NULL`**; `tournament_source_ids` still has **zero** ODDSPAPI
  rows. The independent mapping/edition gate therefore remains closed.
- No API-Tennis external id exists in the stored OddsPapi payload; Betradar is
  the only available external bridge (525/525).
- Provider start vs canonical start: median **-20 min**, 330/525 inside ±30 min,
  431/525 inside ±60 min, range -1,690 to +40 min. The long negative tail is a
  reschedule/metadata issue, not a fixed offset.

## 4. Simultaneous acquisition and entitlement

- API-Tennis entitlement: **ENTITLEMENT_ONLY**. Historical direct access is
  proven (22,515 raw observations, 101,172 normalized quotes, 13 bookmakers),
  current quota is **unproven**. `may_make_billable_requests = false`; this work
  made **0** billable calls.
- Pinnacle is available through the current OddsPapi plan (`pinnacle+30`,
  `SHARP`) and has current prices. API-Tennis `PNCL` is historical; its current
  entitlement is unproven.
- 514 canonical matches have both API-Tennis and OddsPapi identity rows, but
  **0** have normalized prices from both providers. Same-book overlap is also
  zero. A genuine identical-fixture comparison still requires prospective
  simultaneous capture.
- Off-season balanced cohort: in the next 14 days there are **0 ATP and 23
  Challenger** fixtures. The 80/80 target cannot be met; shortfall is 80 ATP and
  57 Challenger. No other competition was substituted.
- The planner builds one discovery request plus one odds request per book per
  five tournaments, and one date-scoped API-Tennis odds request. It removes
  duplicate `(provider, endpoint, identifiers, window, bookmaker)` requests and
  refuses execution without `VERIFIED` entitlement.

## 5. Market benchmark methodology

All stored quotes remain later than first issue: **0 original-time two-sided
comparisons** in every population. The retrospective block is therefore labelled
`LATE_OR_BACKFILLED_DESCRIPTIVE_ONLY` and is never used for an edge claim.

Top-800-by-confidence, all tours (365 retrospective priced fixtures):

| Market definition | Coverage | Market Brier | Market log loss | Market accuracy | Agent 1 Brier | Agent 1 accuracy |
|---|---:|---:|---:|---:|---:|---:|
| Pinnacle reference | 250 | 0.14430 | 0.44764 | 0.7800 | 0.19281 | 0.7520 |
| Multi-book consensus | 365 | 0.14658 | 0.45768 | 0.7863 | 0.18223 | 0.7699 |
| Consensus, duplicate pairs collapsed | 365 | 0.14663 | 0.45777 | 0.7863 | 0.18223 | 0.7699 |

There are 108 exact duplicate price pairs among 1,144 book pairs (13 books).
Collapsing them changes market Brier by only +0.00005, so the duplicate issue is
real but not the driver of the aggregate result. The paired model-minus-market
Brier interval excludes zero in the model-worse direction for all tours
(+0.0162 to +0.0557) and Challenger, but spans zero for ATP. CLV is unavailable
because no original-time entry quote exists.

A/B/C/D support is implemented in
`src/sports_edge_tennis/validation/market_benchmark_methodology.py`; the
market-assisted arm stays unavailable below 200 original-time fixtures.

## 6. Manager C cohort reconciliation

The published counts reproduce exactly from the registered per-fixture arms:

| Measure | Value |
|---|---:|
| Settled eligible | **599** |
| Manager C retained (corrected A1) | **254** |
| Manager abstained | **345** |
| Common support (seven covered arms) | **218** |
| Original Manager retained (R2) | **283** |

**The 36-fixture difference is fully explained.** Common support is the
intersection of every covered arm. On a corrected-Manager selection the A1/A2
mean arms always cover, so the binding constraint is the *original* Manager arm
(R2). Exactly 36 corrected-Manager selections were not selected by R2. In the
other direction 65 R2 selections were not selected by the corrected Manager.
Union = 319, intersection = 218, unexplained = 0. The original Manager selects a
different set because corrected Agent 1 moves the A1/A2 agreement test.

Paired accounting on all 254 shared fixtures: 8 side flips, 5 A1-only correct,
3 Manager-only correct; exact McNemar p = **0.7265625**. There is no reliable
winner-picking difference. E versus R2 on their 218 shared fixtures has one side
flip (1-0, p = 1.0).

Forecast averaging over the 466 A2-available fixtures lowers sharpness (mean
absolute deviation 0.1542 → 0.1291) and accuracy (0.6266 → 0.6073), while Brier
moves only from 0.22245 to 0.22147 (paired interval -0.00572 to +0.00762, spans
zero). It is not evidence of a better forecast.

## 7. Agent 2 specialist audit

Existing mechanisms (read from the real configuration, not proposed):

- surface and surface/tour hierarchical shrinkage with fitted `k` values;
- minimum 5 prior matches, 250 service points, 250 return points;
- one-sided history abstains; PIT state must resolve; deterministic surface
  resolution; versioned scoring rules.

Not implemented in the inspected code: recency/inactivity weighting,
opponent-strength adjustment, explicit statistical uncertainty intervals.

Pre-registered specialist categories (settled, both agents present):

| Category | n | Agent 1 Brier | Agent 2 Brier | Verdict |
|---|---:|---:|---:|---|
| Well-supported both (≥10 matches, ≥500 serve/return points) | 82 | 0.24400 | **0.23412** | INSUFFICIENT_EVIDENCE (n < 100) |
| Minimum supported | 257 | **0.23449** | 0.23611 | INSUFFICIENT_EVIDENCE |
| Substantial disagreement (≥0.10) | 253 | **0.22018** | 0.23590 | INSUFFICIENT_EVIDENCE |
| Well-supported + disagreement | 35 | 0.25563 | **0.23213** | INSUFFICIENT_EVIDENCE (n < 100) |

No specialist category passes the pre-registered gate (n ≥ 100 and a paired
Brier interval entirely in Agent 2's favour). Agent 3 abstained on 599/599 and
its support gate was not relaxed. No best-of-five evidence exists in this
window; AO/BO5 work remains coordinated with PR #389.

## 8. Delivery status

Implemented:

- provider attribution read model and commercial coverage report;
- real-format OddsPapi regression test (runs on PostgreSQL CI);
- measured acquisition-delay decomposition and additive stage attribution;
- versioned market catalogue and payload/orientation diagnostics;
- API-Tennis/OddsPapi simultaneous request planner with entitlement gate;
- benchmark methodology (A/B/C/D, reference vs consensus vs de-duplicated,
  paired bootstrap, CLV refusal);
- Manager cohort reconciliation and exact 36-fixture explanation;
- Agent 2 specialist audit and support gate;
- reproducible read-only evidence scripts and JSON artifacts.

Validation: new unit tests pass locally. The exact code head `f3f0e35` passed
the PostgreSQL Foundation workflow (run 45939666768) with **2,561 passed**,
including the real-format OddsPapi insert/coverage regression test. Local
PostgreSQL was unavailable in this session, so that insert test was validated by
CI rather than locally.

Outstanding blockers:

- zero original-time top-800 comparisons until request scheduling changes;
- ODDSPAPI tournament mapping and edition end dates still absent;
- API-Tennis current quota/entitlement unproven without an approved call;
- no simultaneous same-fixture price overlap; off-season cohort is 0 ATP / 22
  Challenger, so 80/80 cannot be reached honestly;
- actual competitive start is not persisted;
- Agent 2 specialist evidence is underpowered; Agent 3 has zero coverage;
- no production deployment, purchase, migration or policy change was made.
