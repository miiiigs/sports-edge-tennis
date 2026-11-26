# Independent audit continuation — results, commits and blockers (2026-11-26)

Integration branch: `integration/offseason-evidence-foundation-20261125`
(PR #392). Research/engineering record only. No production write, deployment,
merge, provider call, purchase or forecasting-policy change. The production
ledger remains at `0022_defaulted_outcome`; 0023/0024 are absent. The registered
prospective Manager evaluation was not retrained, refrozen or scored.

## Commits in this continuation

| Commit | Purpose |
|---|---|
| `9fbeece` | complete marginal Challenger calibration reconciliation |
| `8bcdccb` | fixed Manager C A-versus-B identity comparison |
| `4bd34a7` | OddsPapi 23-pair funnel reconciliation + pre-issue ordering |
| `81483b8` | Agent 1 top-800 commercial interpretation |
| `f794798` | evidence-preserving down-migration guards |
| `8ee6421` | idempotent rollback guard when a table is absent |
| `fa257d4` | late vs stale vs backfilled quote distinction |
| `4ceb453` | quote-state/no-extrapolation tests |
| `c6278f9` | explicit residual bridge-timing caveat |

## Phase 1 — marginal Challenger

- Exact reconciliation: 143 settled, **92 wins**, **94.559351 expected**,
  residual −2.559, Brier 0.227587, log loss 0.647396.
- Bands: 0.60–0.65 → 54 settled, 28 wins, 34.361 expected (−6.361);
  0.65–0.70 → 82, 61, 55.274 (+5.726); 0.70–0.707 → 7, 3, 4.924 (−1.924).
  0.55–0.60, 0.75–0.80 and 0.80+ are measured zeros by the frozen
  0.62133/0.7070145 cutoffs.
- Independent winner variance 31.952 (SD 5.653); player-clustered variance
  71.037 (SD 8.428, design effect 2.223); pooled residual z −0.417.
- **Measured recovery:** full marginal selected population 16 raw <20 → **15
  recovered**, 1 unresolved. Challenger-only settled 143: 15 raw <20 → **14
  recovered**, same unresolved identity. No genuine newcomer population; ITF
  transition and inactivity remain unresolved; no blanket penalty.
- Evidence: `docs/research/MANAGER_C_MARGINAL_CALIBRATION_20261126.md`,
  `docs/research/evidence/manager_c_marginal_calibration_real_20261126.json`.

## Phase 2 — fixed Manager C identity comparison

- Same 656 eligible fixtures, original cutoffs, stored A2/A3, Manager rule,
  calibration and frozen thresholds.
- A arm reproduces the original candidate on 656/656 with zero policy
  mismatches. A1 replay reproduces 89.8% exactly and 91.3% within 0.005.
- A broad: 275 selected / 256 settled, 176 wins (68.75%), expected 181.831,
  Brier 0.20936.
- B broad: 227 / 211, 150 wins (71.09%), expected 150.431, Brier 0.19385.
- Challenger marginal: A 152/143, 92 wins, expected 94.559, Brier 0.22759;
  B 123/115, 71 wins, expected 76.372, Brier 0.23325. Paired interval spans
  zero; no reliable marginal effect.
- Equal-count comparisons favor B (e.g. Challenger top-20% Brier 0.14128 vs
  0.19417). Exactly 1 shared prediction corrected; 0 newly incorrect.
- No original-time two-sided quote qualifies for any arm; measured blocker, no
  bookmaker comparison.
- Evidence: `docs/research/MANAGER_C_IDENTITY_COMPARISON_20261126.md`,
  `docs/research/evidence/manager_c_identity_comparison_real_20261126.json`.

## Phase 3 — OddsPapi acquisition funnel

Frozen 1,342 first-issued Model Picks:

| Stage | Count |
|---|---|
| Original predictions | 1,342 |
| Current OddsPapi identities / at issue | 214 / **1** |
| Raw-price fixtures / at issue | 213 / **1** |
| Two-sided stored markets | 676 |
| Quotes observed before issue | 23 fixtures / 688 rows |
| Quotes received before issue | 23 fixtures / 678 rows |
| Late-ingested | 5 fixtures / 10 rows |
| Stale/backfilled observations | 14 fixtures / 218 rows |
| Final qualifying pairs | **0 strict**; 22 freshness-strict; 23 legacy |

The 23 legacy pairs are non-OddsPapi quotes (21 OFFERED, 2 SHARP PINNACLE);
only one currently has an OddsPapi identity/price. Latest corpus: 3,905
predictions, 509 identities, 505 raw prices, 0 before issue. Raw observations
survive (10,939; 0 without a persisted link) but 0 are normalized ODDSPAPI
snapshots. Independent mappings 0; 27/27 cached editions lack end dates;
drift −60…+60 min, median −10.

New `live/preissue_acquisition.py` orders by earliest safe deadline, applies the
existing entitlement/budget rules, caps attempts/wait time and never blocks
forecasting. It is tested but not activated. No provider call or purchase.
Evidence: `docs/research/ODDSPAPI_ACQUISITION_FUNNEL_20261126.md`,
`docs/research/evidence/odds_acquisition_funnel_real_20261126.json`,
`..._strict_probe_20261126.json`, `oddspapi_capture_timing_real_20261126.json`.

## Phase 4 — Agent 1 top-800

- top800 by confidence: 595/800 wins vs 676.848 expected (z −8.10), Brier
  0.20075.
- latest800 settled: 509/800 vs 534.929 (z −2.00), Brier 0.23220.
- Challenger top800: 579/800 vs 656.148 (z −7.18).
- **Original-time two-sided bookmaker coverage: 0/800 in every population.**
  All 244–383 stored quotes are late; 417–556 are unmatched. No de-vig,
  bookmaker-expected wins or odds distribution is manufactured.
- No DeepSeek 1 handoff artifact was present; exact IDs/hashes are published.
- Evidence: `docs/research/AGENT1_TOP800_COMMERCIAL_INTERPRETATION_20261126.md`,
  `docs/research/evidence/agent1_top800_bookmaker_real_20261126.json`.

## Phase 5 — schema, rollback and release readiness

- 0023/0024 remain reserved; central PR #390 must not renumber or duplicate.
- Down migrations now refuse to drop a non-empty evidence table, and are
  idempotent when the table is absent. Ordinary application rollback is code
  only and preserves both tables and rows.
- Tests prove a populated snapshot/history table survives the down attempt.
- Lock safeguards: set `lock_timeout` 5–10 s and `statement_timeout` 120 s;
  measured 0023 index build 428.6 ms / transaction 588.8 ms, 0024 5.7 ms.
- Schema handoff: `docs/research/OFFSEASON_EVIDENCE_SCHEMA_AND_ROLLBACK_REQUIREMENTS_20261126.md`.

## Validation

- Targeted PostgreSQL + research set: **86 passed** (new modules, existing
  Manager C, capture sequence, identity cache/history/export/funnel, frozen
  manifest).
- Schema-guard, integration and snapshot-persistence set: **18 passed**.
- New pure suites: 24 passed; commercial quote-state suite: 4 passed; the two
  explicit rollback-guard tests: 2 passed.
- Local full-suite PostgreSQL run was not repeated; CI `postgres-foundation` is
  the authoritative full-suite result. On head `7ff99af` it passed **2,499
  tests in 196.46 s** (run 45758947102); GitGuardian passed. The prior baseline
  was 2,469 passed, so the continuation added 30 passing tests.

## Remaining release blockers

1. Production 0023/0024 are not applied; owner approval and a migration window
   are required.
2. Identity and prices are still acquired after first issue; the ordering
   implementation is not activated.
3. Independent OddsPapi tournament mappings 0; 27/27 cached edition end dates
   missing.
4. Provider-vs-canonical start semantics unresolved (three tolerances).
5. No normalized OddsPapi prices; raw observations survive but do not qualify.
6. Historical cache state before 0024 cannot be reconstructed.
7. The high-confidence top-800 A1 slice is overconfident on the inspected
   window; no original-time market benchmark exists.
