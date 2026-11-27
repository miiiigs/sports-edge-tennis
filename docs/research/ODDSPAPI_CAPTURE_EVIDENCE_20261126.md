# OddsPapi capture evidence and pre-issue sequencing (2026-11-26)

Research-only, read-only production session. No migration, deployment, provider
request, purchase or matching-threshold change. Probe commit: `ac85f34`
(integration PR #392). Probe:
`ops/validation/oddspapi_capture_evidence_probe.py`; evidence JSON:
`docs/research/evidence/oddspapi_capture_evidence_real_20261126.json`.

Reproduce:

```
python ops/validation/oddspapi_capture_evidence_probe.py
```

## 1. Independent tournament mappings and edition end dates

| Provider | `tournament_source_ids` rows |
|---|---|
| SACKMANN | 2,289 |
| API_TENNIS | 310 |
| SPORTRADAR | 66 |
| **ODDSPAPI** | **0** |

- `oddspapi_event_identity_cache`: **135 rows** in the final repeatable-read
  snapshot (133 at the first measurement; the scheduled live cron added two
  identities during the session), 27 distinct OddsPapi tournament IDs.
- Canonical editions behind those cached identities: 27; **27 / 27 have
  `end_date IS NULL`**.
- Whole database: 2,579 editions, **360 with `end_date IS NULL`**.

No mapping was created or inferred. The export gate requires an independent
`ODDSPAPI` source mapping **and** a populated edition window, so it cannot pass
for any cached tournament. The cache's own `oddspapi_tournament_id` is
deliberately not treated as independent proof.

## 2. Provider start vs canonical start: full measured distribution

The prior deep dive measured 8 fixtures at −40…−60 minutes. Measuring all 496
canonical fixtures with raw OddsPapi observations (latest observation per
fixture, provider `startTime` minus the canonical match version visible at that
observation time) gives a different, more complete picture:

| Statistic | Value |
|---|---|
| Matched fixtures | 496 |
| Minimum | −60 min |
| Median | **−10 min** |
| Maximum | +60 min |
| Inside ±30 min | 428 / 496 (86.3%) |
| Inside ±60 min | 496 / 496 (100%) |
| Provider earlier | 313 |
| Exact tie | 153 |
| Provider later | 30 |

Bucket counts: −60…−42: 21; −40…−25: 89; −20…−5: 203; 0…+20: 168;
+25…+40: 9; +45…+60: 6.

So the 8-fixture −40/−50/−60 pattern is the **tail**, not the typical case. The
mismatch is real but bounded at ±60 in this corpus and is not a fixed offset.
No tolerance was loosened; a 30-minute rule would reject ~13.7% of matched
fixtures.

Method note: `oddspapi_event_identity_cache.scheduled_start` is populated from
the **canonical** `match_versions.match_start_at` (`live/batch.py`), not from the
provider payload. Measuring drift against the cache therefore yields 0 by
construction. Drift must be measured from `raw_odds_observations.payload_json`
`startTime`, as done here.

## 3. Pre-issue identity and price sequencing

Across **3,900** fixtures with original predictions in the same repeatable-read
snapshot (3,899 at the first measurement; the hourly live run added one):

| Check | Result |
|---|---|
| With an OddsPapi identity (`match_source_ids`) | 500 |
| Identity recorded at or before the prediction `as_of` | **0** |
| Identity recorded at or before prediction lock | **0** |
| Median identity lag after `as_of` | **36,167 s (~10.0 h)** |
| Fixtures with a raw OddsPapi price | 496 |
| Price received at or before the prediction `as_of` | **0** |
| Median price lag after `as_of` | **39,592 s (~11.0 h)** |

The live cycle still resolves identity and captures prices **after** first issue.
A same-cycle 47-second identity is still late; it is classified as late, never
promoted to coverage.

Offline classification (`validation/prospective_capture_sequence.py`) is the
measurement contract. An isolated rehearsal was added: identity row recorded →
price observed and ingested → prediction locked, with every clock before the
cutoff, is classified `PRE_CUTOFF_COMPLETE`; the archived late same-cycle case is
classified `IDENTITY_LATE+PRICE_LATE`. Both pass on PostgreSQL with the real
schema. The runtime ordering itself was **not** changed: it needs its own
approval and a provider-quota plan.

## 4. Source observation, ingestion and identity-availability clocks

Raw OddsPapi observations: 10,795 across 496 fixtures, 2026-09-23 to
2026-11-25, four bookmakers in the payload.

| Clock | Measured |
|---|---|
| Observation basis | `RECEIVE_TIME` 10,779; `PROVIDER_TIME` 16 |
| Ingest lag (`created_at − received_at`) p50 | 0.62 s |
| p90 | 1.86 s |
| Maximum | 46.47 s |
| Identity cache `last_verified_at` range | 2026-09-24 → 2026-11-25 |

Ingestion is fast; the problem is **ordering**, not write latency. The raw
observation timestamp is receive time for almost all rows, so a "provider said
it earlier" claim cannot be reconstructed from `observation_time` for those
rows.

## 5. Genuinely recovered qualifying prices vs counterfactual

| Layer | Count |
|---|---|
| Raw OddsPapi fixtures | 496 |
| Normalised `odds_snapshots` with `source_system='ODDSPAPI'` | **0** |
| Cached identities with a raw price | 131 |
| Independent tournament mappings | **0** |
| Genuinely recovered qualifying pairs | **0** |
| Counterfactual pairs (mapping + start alignment assumed) | 18 |

The 18 pairs remain a labelled counterfactual, not recovered coverage. Nothing
was purchased, fetched or mapped.

## 6. Failure isolation and provider-call discipline

Targeted tests (local PostgreSQL, real migrations):

- `test_oddspapi_identity_cache.py`, `test_oddspapi_identity_history.py`,
  `test_odds_identity_market_export.py`, `test_odds_identity_market_funnel.py`,
  `test_oddspapi_transport.py`, `test_prospective_capture_sequence.py`:
  **64 passed**.
- These include injected trigger failures proving both history/cache directions
  roll back atomically, fail-open behaviour before 0024 exists, and read-only
  partition auditing.

Provider ledger (the production live cron is still running; state advanced
between reads):

- Before this session: 2,070 rows, latest 2026-11-25T17:05:13Z.
- After the read-only work: 2,074 rows, latest 2026-11-25T18:03:56Z.
- The 4 new rows (1 fixtures, 3 tournament odds) are the **scheduled 18:00Z
  production live-runner cron**, not this session. The probe itself made 0
  provider calls and 0 database writes.
- Endpoint totals: `oddspapi tournament odds` 1,229; `oddspapi fixtures` 841.

No matching threshold was loosened, no mapping fabricated, no cache row mutated.

## 7. Remaining blockers

1. Identity and prices are still captured after first issue. The measurement
   path and the isolated complete-order rehearsal exist; the runtime reorder
   does not.
2. Independent OddsPapi tournament mappings: 0. Edition windows: 27/27 cached
   editions missing `end_date`; 360/2,579 overall.
3. Start-time semantics unresolved. Full drift is −60…+60 min, median −10; a
   single threshold cannot represent it without a scheduling-semantics study.
4. No normalised OddsPapi prices. 10,795 raw observations are stored but 0 are
   qualified canonical snapshots.
5. Identity history is only reproducible for cycles after 0024 is applied; past
   cache state was destroyed and cannot be reconstructed.
